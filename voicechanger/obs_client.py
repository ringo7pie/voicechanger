"""OBS Studio（obs-websocket v5）との接続と、録画状態の監視。

接続情報は data/obs_websocket.json（tools/install_audio_tools.ps1 が OBS の設定と同時に書く）。
obsws_python は接続時に INFO でパスワードを含む行をログに出す（baseclient.py）ので、そのロガーは止める。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR

OBS_CONNECTION_PATH = DATA_DIR / "obs_websocket.json"
log = logging.getLogger("voicechanger.obs")


def load_connection(path: Path = OBS_CONNECTION_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return {"host": str(document.get("host", "127.0.0.1")), "port": int(document.get("port", 4455)),
                "password": str(document["password"])}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        log.warning("OBS の接続情報を読めません（%s）: %s", path, error)
        return None


class ObsConnection:
    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout
        self._client: Any = None

    def connect(self) -> None:
        import obsws_python

        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
        self._client = obsws_python.ReqClient(host=self.host, port=self.port, password=self._password,
                                              timeout=self.timeout)

    @property
    def connected(self) -> bool:
        return self._client is not None

    def request(self, request_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._client is None:
            self.connect()
        response = self._client.send(request_type, data, raw=True)
        return response or {}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def __enter__(self) -> "ObsConnection":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class ObsWatcher:
    """OBS の録画状態を 0.5 秒ごとに見る。録画が始まったら on_change(True) を呼ぶ。

    is_recording() は最後に見た値を返すだけ（処理ループから呼ばれるので、通信はしない）。
    """

    def __init__(self, host: str, port: int, password: str, poll_seconds: float = 0.5,
                 retry_seconds: float = 3.0) -> None:
        self._connection = ObsConnection(host, port, password, timeout=2.0)
        self._poll_seconds = poll_seconds
        self._retry_seconds = retry_seconds
        self._recording = False
        self._connected = False
        self._last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_change: Callable[[bool], Any] | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "ObsWatcher | None":  # noqa: ARG003 - 将来の設定用
        connection = load_connection()
        return cls(**connection) if connection else None

    def is_recording(self) -> bool:
        return self._recording

    def describe(self) -> dict[str, Any]:
        return {"connected": self._connected, "recording": self._recording, "last_error": self._last_error}

    def start(self, on_change: Callable[[bool], Any]) -> None:
        self._on_change = on_change
        self._thread = threading.Thread(target=self._loop, name="obs-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._connection.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                status = self._connection.request("GetRecordStatus")
                recording = bool(status.get("outputActive", False))
                if not self._connected:
                    log.info("OBS に接続しました")
                self._connected = True
                self._last_error = ""
                if recording != self._recording:
                    self._recording = recording
                    log.info("OBS の録画: %s", "開始" if recording else "停止")
                    if self._on_change is not None:
                        try:
                            self._on_change(recording)
                        except Exception as error:  # noqa: BLE001
                            log.warning("録画状態の通知に失敗: %s", error)
                wait = self._poll_seconds
            except Exception as error:  # noqa: BLE001 - OBS が起動していないのは普通の状態
                if self._connected:
                    log.info("OBS との接続が切れました: %s", error)
                self._connected = False
                self._recording = False
                self._last_error = f"{type(error).__name__}: {error}"
                self._connection.close()
                wait = self._retry_seconds
            self._stop.wait(wait)
