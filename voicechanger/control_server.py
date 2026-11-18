"""操作 API と操作画面（127.0.0.1 だけで待ち受ける）。

他のページからの書き換えを防ぐため:
- Host ヘッダが 127.0.0.1 / localhost（＋ポート）でなければ拒否する（DNS の付け替え対策）。
- 書き換え（POST）は、Content-Type: application/json、起動ごとのトークン（X-VoiceChanger-Token）、
  Origin（付いていれば http://127.0.0.1:port か http://localhost:port）をすべて満たすときだけ受ける。
  application/json はブラウザが事前確認（preflight）を挟むので、他サイトからの単純な送信は届かない。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .engine import ModeUnavailableError, RecordingGuardError

log = logging.getLogger("voicechanger.control")

WEB_DIR = Path(__file__).resolve().parent / "web"
TOKEN_HEADER = "X-VoiceChanger-Token"
MAX_BODY = 16 * 1024


class ControlServer:
    def __init__(self, engine: Any, host: str, port: int, *, extra_status: Callable[[], dict] | None = None,
                 on_shutdown: Callable[[], None] | None = None, token: str | None = None) -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("操作 API は 127.0.0.1 だけで待ち受ける")
        self.engine = engine
        self.token = token or secrets.token_urlsafe(24)
        self.extra_status = extra_status
        self.on_shutdown = on_shutdown
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="control-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ------------------------------------------------------------ 処理
    def status(self) -> dict[str, Any]:
        status = self.engine.status()
        if self.extra_status is not None:
            try:
                status.update(self.extra_status())
            except Exception as error:  # noqa: BLE001
                status["extra_status_error"] = str(error)
        return status

    def handle_post(self, path: str, body: dict[str, Any]) -> Any:
        engine = self.engine
        if path == "/api/mode":
            return engine.submit("set_mode", mode=str(body["mode"]))
        if path == "/api/dsp":
            if "preset" in body:
                preset = next((item for item in engine.settings.dsp_presets if item["name"] == body["preset"]), None)
                if preset is None:
                    raise ValueError(f"プリセット {body['preset']!r} はありません")
                return engine.submit("set_dsp", semitones=preset["semitones"], formant_factor=preset["formant_factor"])
            return engine.submit("set_dsp", semitones=_optional_float(body, "semitones"),
                                 formant_factor=_optional_float(body, "formant_factor"))
        if path == "/api/ai":
            voice_index = body.get("voice_index")
            return engine.submit("set_ai", voice_index=None if voice_index is None else int(voice_index),
                                 average_source_pitch=_optional_float(body, "average_source_pitch"),
                                 formant_shift=_optional_float(body, "formant_shift"),
                                 pitch_offset=_optional_float(body, "pitch_offset"),
                                 output_gain_db=_optional_float(body, "output_gain_db"))
        if path == "/api/output_gain":
            return engine.submit("set_output_gain", output_gain_db=float(body["output_gain_db"]))
        if path == "/api/monitor":
            return engine.submit("set_monitor", enabled=bool(body["enabled"]))
        if path == "/api/shutdown":
            if self.on_shutdown is not None:
                threading.Thread(target=self.on_shutdown, daemon=True).start()
            return {"shutdown": True}
        raise LookupError(path)

    def render_index(self) -> bytes:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return html.replace("__VOICECHANGER_TOKEN__", self.token).encode("utf-8")


def _optional_float(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    return None if value is None else float(value)


def _make_handler(server: ControlServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VoiceChanger"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 親の引数名に合わせる
            log.debug("%s - %s", self.address_string(), format % args)

        # ---------------------------------------------------- 共通
        def _send(self, status: int, payload: Any, content_type: str = "application/json; charset=utf-8") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            self._send(status, {"error": message})

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in server.allowed_hosts

        # ---------------------------------------------------- GET
        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                self._error(HTTPStatus.FORBIDDEN, "Host が許可されていません")
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, server.render_index(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self._send(HTTPStatus.OK, server.status())
            else:
                self._error(HTTPStatus.NOT_FOUND, "ありません")

        # ---------------------------------------------------- POST
        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                self._error(HTTPStatus.FORBIDDEN, "Host が許可されていません")
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in server.allowed_origins:
                self._error(HTTPStatus.FORBIDDEN, "Origin が許可されていません")
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type は application/json にしてください")
                return
            if not secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), server.token):
                self._error(HTTPStatus.FORBIDDEN, "トークンが違います")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._error(HTTPStatus.BAD_REQUEST, "本文の長さが不正です")
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("本文は JSON のオブジェクトにしてください")
                result = server.handle_post(self.path.split("?", 1)[0], body)
            except LookupError as error:
                if isinstance(error, KeyError):
                    self._error(HTTPStatus.BAD_REQUEST, f"項目がありません: {error}")
                else:
                    self._error(HTTPStatus.NOT_FOUND, "ありません")
                return
            except (ModeUnavailableError, RecordingGuardError) as error:
                self._error(HTTPStatus.CONFLICT, str(error))
                return
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except TimeoutError as error:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
                return
            except Exception as error:  # noqa: BLE001
                log.exception("API の処理で例外")
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")
                return
            self._send(HTTPStatus.OK, {"ok": True, "result": result, "status": server.status()})

        def do_OPTIONS(self) -> None:  # noqa: N802 - 事前確認には応えない（他サイトからの書き換えを通さない）
            self._error(HTTPStatus.FORBIDDEN, "許可されていません")

    return Handler
