"""GUI の判断とエンジンとのつなぎ（Qt を使わない部分）の試験。"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.test_control_server import FakeEngine
from voicechanger.control_server import ControlServer
from voicechanger.engine import RecordingGuardError
from voicechanger.gui import view_state
from voicechanger.gui.engine_link import (RUNNING, STOPPED, ApiClient, ApiError, CommandSender, Control, EngineLink,
                                          engine_command, last_engine_message, read_control)

INPUTS = ["マイク (High Definition Audio Device)"]
OUTPUTS = ["ヘッドホン (High Definition Audio Device)", "スピーカー (Steam Streaming Speakers)"]
HEADPHONES = OUTPUTS[0]


class ChooseDevicesTest(unittest.TestCase):
    def test_output_prefers_saved_then_cable_then_headphones(self) -> None:
        with_cable = OUTPUTS + ["CABLE Input (VB-Audio Virtual Cable)"]
        self.assertEqual(view_state.choose_output(OUTPUTS[1], with_cable, HEADPHONES), OUTPUTS[1])
        self.assertEqual(view_state.choose_output(None, with_cable, HEADPHONES), "CABLE Input (VB-Audio Virtual Cable)")
        self.assertEqual(view_state.choose_output("消えた機器", OUTPUTS, HEADPHONES), HEADPHONES)
        self.assertEqual(view_state.choose_output(None, ["何か"], HEADPHONES), "何か")
        self.assertIsNone(view_state.choose_output(None, [], HEADPHONES))

    def test_input_and_monitor(self) -> None:
        self.assertEqual(view_state.choose_input(None, INPUTS, INPUTS[0]), INPUTS[0])
        self.assertEqual(view_state.choose_input("無い", ["別のマイク"], "無い"), "別のマイク")
        self.assertEqual(view_state.choose_monitor(None, OUTPUTS, "ヘッドホン (high definition audio device)"), HEADPHONES)
        self.assertIsNone(view_state.choose_monitor(None, OUTPUTS, "無い"))


class StatusViewTest(unittest.TestCase):
    def test_warnings(self) -> None:
        status = {"mode": "through", "through_remaining_s": 42.3, "monitor": True, "notice": "お知らせ",
                  "unavailable": {"ai": "VST が無い"}}
        texts = [warning.text for warning in view_state.warnings_from_status(status, HEADPHONES)]
        self.assertTrue(any("素の声" in text and "43 秒" in text for text in texts))
        self.assertTrue(any("モニター" in text for text in texts))
        self.assertTrue(any("CABLE Input" in text for text in texts), "出力が仮想マイクでなければ案内する")
        self.assertIn("お知らせ", texts)
        self.assertTrue(any("AI（Beatrice） は使えません" in text for text in texts))
        quiet = view_state.warnings_from_status({"mode": "dsp"}, "CABLE Input (VB-Audio Virtual Cable)")
        self.assertEqual(quiet, [])

    def test_default_render_warning_only_for_cable(self) -> None:
        warning = view_state.default_render_warning("CABLE Input (VB-Audio Virtual Cable)")
        self.assertIn("既定の再生デバイス", warning)
        self.assertIn("mmsys.cpl", warning)
        self.assertIsNone(view_state.default_render_warning("VP32AQ (NVIDIA High Definition Audio)"))
        self.assertIsNone(view_state.default_render_warning(None))

    def test_input_loop_warning(self) -> None:
        self.assertIn("回り続けます", view_state.input_loop_warning("CABLE Output (VB-Audio Virtual Cable)"))
        self.assertIsNone(view_state.input_loop_warning("マイク (High Definition Audio Device)"))
        self.assertIsNone(view_state.input_loop_warning(None))

    def test_meter_and_format(self) -> None:
        self.assertEqual(view_state.meter_fraction(-60.0), 0.0)
        self.assertEqual(view_state.meter_fraction(0.0), 1.0)
        self.assertAlmostEqual(view_state.meter_fraction(-30.0), 0.5)
        self.assertEqual(view_state.meter_fraction(float("-inf")), 0.0)
        self.assertEqual(view_state.meter_color(-0.5), "clip")
        self.assertEqual(view_state.meter_color(-6.0), "hot")
        self.assertEqual(view_state.meter_color(-20.0), "normal")
        self.assertEqual(view_state.format_db(-180.0), "-∞ dB")
        self.assertEqual(view_state.format_signed(5.0, 1, " 半音"), "+5.0 半音")
        self.assertEqual(view_state.format_signed(0.0), "0.0")

    def test_monitor_available_and_stats(self) -> None:
        self.assertFalse(view_state.monitor_available({"backend": {"devices": {"monitor": None}}}))
        self.assertTrue(view_state.monitor_available({"backend": {"devices": {"monitor": HEADPHONES}}}))
        rows = dict(view_state.stats_rows({"aligned_latency_ms": 78.0, "buffers": {"output_underruns": 2},
                                            "processing": {}, "obs": None}))
        self.assertEqual(rows["遅延（そろえた値）"], "78 ms")
        self.assertEqual(rows["出力の途切れ"], "2")


class EngineCommandTest(unittest.TestCase):
    def test_engine_command_has_parent_pid(self) -> None:
        command = engine_command(Path("python.exe"), 1234, ["--input-wav", "a.wav"])
        self.assertEqual(command[:4], ["python.exe", "-m", "voicechanger", "run"])
        self.assertEqual(command[4:6], ["--parent-pid", "1234"])
        self.assertEqual(command[-2:], ["--input-wav", "a.wav"])

    def test_last_engine_message_picks_failure_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "engine.log"
            log_path.write_text("2026 INFO x: VOICECHANGER_READY url=a\n"
                                "2026 INFO voicechanger: VOICECHANGER_DEVICE_ERROR output デバイス 'X' が見つかりません\n",
                                encoding="utf-8")
            self.assertEqual(last_engine_message(log_path, Path(directory) / "none.log"),
                             "output デバイス 'X' が見つかりません")
            self.assertEqual(last_engine_message(Path(directory) / "none.log", Path(directory) / "none2.log"), "")


class CommandSenderTest(unittest.TestCase):
    def test_pending_sends_to_the_same_path_are_merged(self) -> None:
        gate = threading.Event()
        sent = []
        results = []

        def send(path: str, body: dict) -> dict:
            sent.append((path, dict(body)))
            if len(sent) == 1:
                gate.wait(2.0)
            return {"ok": True}

        sender = CommandSender(send, lambda path, result, error: results.append((path, error)))
        try:
            sender.submit("/api/dsp", {"semitones": 1.0})
            time.sleep(0.05)  # 1 件目が送信中で止まっている間に溜める
            sender.submit("/api/mode", {"mode": "ai"})
            sender.submit("/api/dsp", {"semitones": 2.0})
            sender.submit("/api/dsp", {"semitones": 3.0, "formant_factor": 1.2})
            gate.set()
            self.assertTrue(sender.idle(2.0))
        finally:
            sender.close()
        self.assertEqual(sent, [("/api/dsp", {"semitones": 1.0}), ("/api/mode", {"mode": "ai"}),
                                ("/api/dsp", {"semitones": 3.0, "formant_factor": 1.2})])
        self.assertEqual(len(results), 3)

    def test_errors_are_reported(self) -> None:
        results = []

        def send(path: str, body: dict) -> dict:
            raise ApiError("録画中", 409)

        sender = CommandSender(send, lambda path, result, error: results.append(error))
        try:
            sender.submit("/api/mode", {"mode": "through"})
            self.assertTrue(sender.idle(2.0))
        finally:
            sender.close()
        self.assertIsInstance(results[0], ApiError)
        self.assertEqual(results[0].status, 409)


class LiveServerTest(unittest.TestCase):
    """試験の中で操作 API を立て、GUI 側のつなぎを確かめる。"""

    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.shutdowns = []
        self.server = ControlServer(self.engine, "127.0.0.1", 0, token="tok",
                                    on_shutdown=lambda: self.shutdowns.append(time.monotonic()))
        self.server.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.control_path = Path(self.temporary.name) / "control.json"
        self.control_path.write_text(json.dumps({"pid": os.getpid(), "port": self.server.port, "token": "tok",
                                                 "url": self.server.url}), encoding="utf-8")

    def tearDown(self) -> None:
        self.server.stop()
        self.temporary.cleanup()

    def test_api_client(self) -> None:
        control = read_control(self.control_path)
        self.assertEqual(control, Control(os.getpid(), self.server.port, "tok", self.server.url))
        client = ApiClient(control)
        self.assertEqual(client.status()["mode"], "dsp")
        client.post("/api/mode", {"mode": "ai"})
        self.assertEqual(self.engine.calls[-1], ("set_mode", {"mode": "ai"}))
        self.engine.raise_error = RecordingGuardError("OBS が録画中")
        with self.assertRaises(ApiError) as caught:
            client.post("/api/mode", {"mode": "through"})
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("録画中", str(caught.exception))

    def test_read_control_ignores_dead_pid(self) -> None:
        self.control_path.write_text(json.dumps({"pid": 999999, "port": 1, "token": "x", "url": "u"}), encoding="utf-8")
        self.assertIsNone(read_control(self.control_path))

    def test_attach_poll_post_and_detach(self) -> None:
        states, statuses, results = [], [], []
        link = EngineLink(lambda state, message: states.append(state), statuses.append,
                          lambda path, result, error: results.append((path, error)),
                          control_path=self.control_path, poll_interval=0.02)
        try:
            self.assertTrue(link.attach_existing())
            self.assertEqual(link.state, RUNNING)
            self.assertFalse(link.owned, "起動済みのエンジンは自分のものではない")
            deadline = time.monotonic() + 2.0
            while len(statuses) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(len(statuses), 3)
            link.post("/api/dsp", {"semitones": 4.0})
            self.assertTrue(link.sender.idle(2.0))
            self.assertEqual(self.engine.calls[-1], ("set_dsp", {"semitones": 4.0, "formant_factor": None}))
            self.assertEqual(results[-1], ("/api/dsp", None))
            link.detach()
            self.assertEqual(link.state, STOPPED)
        finally:
            link.close()
        time.sleep(0.05)
        self.assertEqual(self.shutdowns, [], "接続しただけのエンジンには、閉じても停止の命令を送らない")


if __name__ == "__main__":
    unittest.main()
