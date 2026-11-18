import http.client
import json
import unittest

from voicechanger.config import Settings
from voicechanger.control_server import TOKEN_HEADER, ControlServer
from voicechanger.engine import ModeUnavailableError, RecordingGuardError


class FakeEngine:
    def __init__(self) -> None:
        self.calls = []
        self.raise_error: BaseException | None = None
        self.settings = Settings(settings_path=None)

    def submit(self, name, timeout=5.0, **arguments):
        self.calls.append((name, arguments))
        if self.raise_error is not None:
            raise self.raise_error
        return {"mode": arguments.get("mode", "dsp"), "pending_mode": None}

    def status(self):
        return {"mode": "dsp", "buffers": {}}


class ControlServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.server = ControlServer(self.engine, "127.0.0.1", 0, token="secret-token")
        self.server.start()
        self.port = self.server.port

    def tearDown(self) -> None:
        self.server.stop()

    def request(self, method: str, path: str, body: dict | None = None, *, host: str | None = None,
                token: str | None = "secret-token", content_type: str = "application/json",
                origin: str | None = None) -> tuple[int, dict | str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        if token is not None:
            headers[TOKEN_HEADER] = token
        if origin is not None:
            headers["Origin"] = origin
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            return response.status, json.loads(raw)
        except json.JSONDecodeError:
            return response.status, raw

    def test_index_contains_token_only_for_local_host(self) -> None:
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('content="secret-token"', page)
        status, _ = self.request("GET", "/", host="evil.example:80")
        self.assertEqual(status, 403)

    def test_status_is_readable(self) -> None:
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "dsp")

    def test_post_requires_token_json_and_matching_origin(self) -> None:
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"}, token=None)[0], 403)
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"}, token="wrong")[0], 403)
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"}, content_type="text/plain")[0], 415)
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"}, host="evil.example")[0], 403)
        self.assertEqual(self.engine.calls, [], "拒否した要求はエンジンに届かない")
        status, body = self.request("POST", "/api/mode", {"mode": "ai"}, origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(self.engine.calls, [("set_mode", {"mode": "ai"})])
        self.assertTrue(body["ok"])

    def test_preflight_is_refused(self) -> None:
        self.assertEqual(self.request("OPTIONS", "/api/mode", token=None)[0], 403)

    def test_errors_map_to_status_codes(self) -> None:
        self.engine.raise_error = RecordingGuardError("録画中")
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "through"})[0], 409)
        self.engine.raise_error = ModeUnavailableError("無い")
        self.assertEqual(self.request("POST", "/api/mode", {"mode": "ai"})[0], 409)
        self.engine.raise_error = ValueError("範囲外")
        self.assertEqual(self.request("POST", "/api/dsp", {"semitones": 99})[0], 400)
        self.engine.raise_error = None
        self.assertEqual(self.request("POST", "/api/mode", {})[0], 400, "mode が無い")
        self.assertEqual(self.request("POST", "/api/unknown", {})[0], 404)

    def test_dsp_preset_is_expanded(self) -> None:
        preset = self.engine.settings.dsp_presets[0]
        status, _ = self.request("POST", "/api/dsp", {"preset": preset["name"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.engine.calls[-1], ("set_dsp", {"semitones": preset["semitones"],
                                                             "formant_factor": preset["formant_factor"]}))

    def test_server_refuses_non_local_bind(self) -> None:
        with self.assertRaises(ValueError):
            ControlServer(self.engine, "0.0.0.0", 0)


if __name__ == "__main__":
    unittest.main()
