"""GUI とエンジンのつなぎ（Qt に依らない）。

- エンジンのプロセスを起動・停止する（黒い窓を出さない。--parent-pid で GUI が消えたらエンジンも止まる）
- 起動済みのエンジン（Run.ps1 で起動したもの）には接続だけする
- 操作 API を呼ぶ。スライダの連続送信は、同じ宛先なら最新の値だけを送る
- 状態を定期的に取り、コールバックで渡す（Qt 側はシグナルに包んで GUI のスレッドへ渡す）
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..__main__ import CONTROL_PATH, LOG_DIR, pid_alive
from ..paths import APP_DATA, FROZEN, REPO_ROOT
from ..control_server import TOKEN_HEADER

log = logging.getLogger("voicechanger.gui.link")

VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
ENGINE_LOG = LOG_DIR / "engine.log"
ENGINE_STDERR_LOG = LOG_DIR / "engine-stderr.log"
CREATE_NO_WINDOW = 0x08000000

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
ERROR = "error"


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Control:
    pid: int
    port: int
    token: str
    url: str
    parent_pid: int = 0


def read_control(path: Path = CONTROL_PATH) -> Control | None:
    """起動中のエンジンの接続情報。PID が生きていなければ None。

    pid はエンジン本体の PID。Windows の venv の python.exe は中継役で、本物の Python を子プロセスとして起動するので、
    GUI が Popen で得た PID とは一致しない。自分が起動したエンジンかは parent_pid（--parent-pid）で見分ける。
    """
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        control = Control(int(document["pid"]), int(document["port"]), str(document["token"]), str(document["url"]),
                          int(document.get("parent_pid") or 0))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None
    return control if pid_alive(control.pid) else None


class ApiClient:
    def __init__(self, control: Control, timeout: float = 3.0) -> None:
        self.control = control
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        connection = http.client.HTTPConnection("127.0.0.1", self.control.port, timeout=self.timeout)
        try:
            headers = {"Host": f"127.0.0.1:{self.control.port}"}
            data = None
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers[TOKEN_HEADER] = self.control.token
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except OSError as error:
            raise ApiError(f"エンジンにつながりません: {error}") from error
        finally:
            connection.close()
        try:
            document = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(f"エンジンの応答を読めません（{response.status}）", response.status) from error
        if response.status >= 400:
            message = document.get("error") if isinstance(document, dict) else None
            raise ApiError(message or f"エラー {response.status}", response.status)
        return document

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)


class CommandSender:
    """API への送信を 1 本の裏スレッドで順に行う。同じ宛先の送信が溜まっていたら、最新の値だけを送る。"""

    def __init__(self, send: Callable[[str, dict], dict],
                 on_result: Callable[[str, dict | None, Exception | None], None]) -> None:
        self._send = send
        self._on_result = on_result
        self._pending: dict[str, dict] = {}
        self._order: list[str] = []
        self._condition = threading.Condition()
        self._stopped = False
        self._busy = False
        self._thread = threading.Thread(target=self._loop, name="api-sender", daemon=True)
        self._thread.start()

    def submit(self, path: str, body: dict) -> None:
        with self._condition:
            if path in self._pending:
                self._pending[path] = {**self._pending[path], **body}
            else:
                self._pending[path] = dict(body)
                self._order.append(path)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify()
        self._thread.join(timeout=2.0)

    def idle(self, timeout: float = 2.0) -> bool:
        """送信待ちが無くなるまで待つ（試験用）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._condition:
                if not self._order and not self._busy:
                    return True
            time.sleep(0.005)
        return False

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._order and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                path = self._order.pop(0)
                body = self._pending.pop(path)
                self._busy = True
            try:
                result = self._send(path, body)
                self._on_result(path, result, None)
            except Exception as error:  # noqa: BLE001 - 呼び出し側へ渡す
                self._on_result(path, None, error)
            finally:
                with self._condition:
                    self._busy = False


def last_engine_message(log_path: Path = ENGINE_LOG, stderr_path: Path = ENGINE_STDERR_LOG) -> str:
    """エンジンのログから、起動の失敗の理由になりそうな最後の 1 行を返す。"""
    candidates: list[str] = []
    for path in (log_path, stderr_path):
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-200:]
        except OSError:
            continue
        for line in lines:
            text = line.strip()
            if any(marker in text for marker in ("VOICECHANGER_DEVICE_ERROR", "VOICECHANGER_START_FAILED",
                                                 "VOICECHANGER_ALREADY_RUNNING", "Error", "ERROR")):
                candidates.append(text)
    if not candidates:
        return ""
    message = candidates[-1]
    for marker in ("VOICECHANGER_DEVICE_ERROR ", "VOICECHANGER_START_FAILED "):
        if marker in message:
            return message.split(marker, 1)[1]
    return message


def engine_prefix(python_exe: Path = VENV_PYTHON, frozen: bool = FROZEN) -> list[str]:
    """エンジンの CLI を呼ぶコマンドの頭。凍結した実行ファイルは自分自身を engine 付きで呼ぶ（voicechanger/app_entry.py）。"""
    if frozen:
        return [sys.executable, "engine"]
    return [str(python_exe), "-m", "voicechanger"]


def engine_command(python_exe: Path, parent_pid: int, extra_arguments: list[str] | None = None,
                   frozen: bool = FROZEN) -> list[str]:
    return [*engine_prefix(python_exe, frozen), "run", "--parent-pid", str(parent_pid), *(extra_arguments or [])]


def list_devices_subprocess(python_exe: Path = VENV_PYTHON, timeout: float = 30.0) -> dict:
    """別プロセスでデバイス一覧を取る（PortAudio の一覧は初期化した時点のものなので、毎回新しく取るため）。

    結果は一時ファイルで受け取る（窓アプリとして凍結した実行ファイルは stdout を持たない）。
    """
    handle, temporary = tempfile.mkstemp(prefix="voicechanger-devices-", suffix=".json")
    os.close(handle)
    result_path = Path(temporary)
    try:
        completed = subprocess.run([*engine_prefix(python_exe), "devices", "--json", "--json-out", str(result_path)],
                                   cwd=APP_DATA, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip() or (completed.stdout or "").strip()
            raise RuntimeError(f"デバイス一覧を取れません: {detail or f'終了コード {completed.returncode}'}")
        return json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        result_path.unlink(missing_ok=True)


class EngineLink:
    """エンジンのプロセスと API をまとめて扱う。コールバックは裏スレッドから呼ばれる。"""

    def __init__(self, on_state: Callable[[str, str], None], on_status: Callable[[dict], None],
                 on_command_result: Callable[[str, dict | None, Exception | None], None], *,
                 python_exe: Path = VENV_PYTHON, control_path: Path = CONTROL_PATH,
                 poll_interval: float = 0.15, start_timeout: float = 60.0) -> None:
        self._on_state = on_state
        self._on_status = on_status
        self._python_exe = python_exe
        self._control_path = control_path
        self._poll_interval = poll_interval
        self._start_timeout = start_timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._client: ApiClient | None = None
        self.owned = False
        self.state = STOPPED
        self.message = ""
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self.sender = CommandSender(self._send, on_command_result)

    # ------------------------------------------------------------ 状態
    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self.message = message
        self._on_state(state, message)

    def _send(self, path: str, body: dict) -> dict:
        client = self._client
        if client is None or self.state != RUNNING:
            raise ApiError("エンジンが動いていません")
        return client.post(path, body)

    def post(self, path: str, body: dict) -> None:
        self.sender.submit(path, body)

    # ------------------------------------------------------------ 接続・起動・停止
    def attach_existing(self) -> bool:
        control = read_control(self._control_path)
        if control is None:
            return False
        self._client = ApiClient(control)
        try:
            self._client.status()
        except ApiError:
            self._client = None
            return False
        self.owned = False
        self._set_state(RUNNING, f"起動済みのエンジンに接続しました（PID {control.pid}）")
        self._start_polling()
        return True

    def start(self, extra_arguments: list[str] | None = None) -> None:
        """裏でエンジンを起動し、準備ができたら RUNNING にする。"""
        with self._lock:
            if self.state in (STARTING, RUNNING, STOPPING):
                return
            self._set_state(STARTING, "エンジンを起動しています（AI モデルの読み込みで数秒かかります）")
        threading.Thread(target=self._start_worker, args=(extra_arguments,), name="engine-start", daemon=True).start()

    def _start_worker(self, extra_arguments: list[str] | None) -> None:
        if read_control(self._control_path) is not None:
            if self.attach_existing():
                return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            stderr_handle = open(ENGINE_STDERR_LOG, "w", encoding="utf-8")
            self._process = subprocess.Popen(
                engine_command(self._python_exe, os.getpid(), extra_arguments), cwd=APP_DATA,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr_handle,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            stderr_handle.close()
        except OSError as error:
            self._set_state(ERROR, f"エンジンを起動できません: {error}")
            return
        self.owned = True
        deadline = time.monotonic() + self._start_timeout
        while time.monotonic() < deadline:
            exit_code = self._process.poll()
            if exit_code is not None:
                reason = last_engine_message() or f"エンジンが終了しました（終了コード {exit_code}）"
                self._process = None
                self.owned = False
                self._set_state(ERROR, reason)
                return
            control = read_control(self._control_path)
            if control is not None and (control.parent_pid == os.getpid() or control.pid == self._process.pid):
                client = ApiClient(control)
                try:
                    client.status()
                except ApiError:
                    time.sleep(0.2)
                    continue
                self._client = client
                self._set_state(RUNNING, "")
                self._start_polling()
                return
            time.sleep(0.2)
        control = read_control(self._control_path)
        self._terminate_process(control.pid if control is not None and control.parent_pid == os.getpid() else 0)
        self._process = None
        self.owned = False
        self._set_state(ERROR, f"エンジンが {self._start_timeout:.0f} 秒以内に起動しませんでした")

    def stop(self, wait: bool = False) -> None:
        """エンジンを止める（接続しただけのエンジンも止める）。"""
        with self._lock:
            if self.state not in (RUNNING, STARTING, ERROR):
                return
            previous = self.state
            self._set_state(STOPPING, "エンジンを止めています")
        worker = threading.Thread(target=self._stop_worker, args=(previous,), name="engine-stop", daemon=True)
        worker.start()
        if wait:
            worker.join(timeout=20.0)

    def _stop_worker(self, previous_state: str) -> None:
        self._stop_polling()
        client = self._client
        engine_pid = client.control.pid if client is not None else 0
        if client is not None and previous_state == RUNNING:
            try:
                client.post("/api/shutdown", {})
            except ApiError as error:
                log.warning("停止の命令を送れませんでした: %s", error)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and self._any_alive(engine_pid):
            time.sleep(0.1)
        if self._any_alive(engine_pid):
            self._terminate_process(engine_pid)
        self._client = None
        self._process = None
        self.owned = False
        self._set_state(STOPPED, "")

    def _any_alive(self, engine_pid: int) -> bool:
        launcher_alive = self._process is not None and self._process.poll() is None
        return launcher_alive or (engine_pid > 0 and pid_alive(engine_pid))

    def _terminate_process(self, engine_pid: int = 0) -> None:
        """止まらないときの最後の手段。中継役と本体の両方を終了させる（control.json は残るが、PID の確認で無視される）。"""
        if self._process is not None and self._process.poll() is None:
            log.warning("エンジンが止まらないので終了させます（中継役 PID %s）", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if engine_pid > 0 and pid_alive(engine_pid):
            log.warning("エンジン本体を終了させます（PID %s）", engine_pid)
            try:
                os.kill(engine_pid, 9)
            except OSError as error:
                log.warning("終了させられませんでした: %s", error)

    def detach(self) -> None:
        """接続をやめる（エンジンは止めない）。"""
        self._stop_polling()
        self._client = None
        self._set_state(STOPPED, "")

    def close(self) -> None:
        """GUI を閉じるとき。自分で起動したエンジンだけ止める。"""
        if self.owned and self.state in (RUNNING, STARTING):
            self.stop(wait=True)
        else:
            self._stop_polling()
        self.sender.close()

    # ------------------------------------------------------------ 状態の取得
    def _start_polling(self) -> None:
        self._stop_polling()
        self._poll_stop = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, args=(self._poll_stop,), name="status-poll",
                                             daemon=True)
        self._poll_thread.start()

    def _stop_polling(self) -> None:
        self._poll_stop.set()
        thread = self._poll_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._poll_thread = None

    def _poll_loop(self, stop: threading.Event) -> None:
        failures = 0
        while not stop.wait(self._poll_interval):
            client = self._client
            if client is None:
                return
            try:
                self._on_status(client.status())
                failures = 0
            except ApiError:
                failures += 1
                if failures >= 5 and not pid_alive(client.control.pid):
                    self._client = None
                    self._process = None
                    owned = self.owned
                    self.owned = False
                    reason = last_engine_message() if owned else ""
                    self._set_state(ERROR if owned else STOPPED,
                                    f"エンジンが止まりました。{reason}".strip() if owned else "エンジンが止まりました")
                    return
