"""メインウィンドウの試験（画面を出さない offscreen で、エンジンへの接続は偽物）。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 が入っていない
    QApplication = None

from voicechanger.config import DEFAULT_CONFIG_PATH, Settings

INPUTS = ["マイク (High Definition Audio Device)"]
OUTPUTS = ["ヘッドホン (High Definition Audio Device)", "スピーカー (Steam Streaming Speakers)"]


def running_status(**overrides) -> dict:
    status = {
        "mode": "dsp", "pending_mode": None, "available_modes": ["dsp", "ai", "mute", "through"], "unavailable": {},
        "notice": "", "monitor": False, "through_remaining_s": None, "output_gain_db": 0.0, "aligned_latency_ms": 78.0,
        "levels": {"input_peak_db": -20.0, "output_peak_db": -18.0},
        "buffers": {"output_underruns": 0, "drift_drops": 0, "drift_inserts": 0, "forced_drops": 0, "processor_errors": 0},
        "processing": {"average_block_ms": 0.6, "worst_block_ms": 3.0},
        "processors": {
            "dsp": {"semitones": 5.0, "formant_factor": 1.15},
            "ai": {"voices": [{"index": 0, "name": "声 A"}, {"index": 1, "name": "声 B"}], "voice_index": 1,
                   "pitch_offset": 0.0, "average_source_pitch": 43.875, "formant_shift": 0.0, "output_gain_db": 6.0,
                   "pitch_shift": 7.0},
        },
        "backend": {"devices": {"input": INPUTS[0], "output": OUTPUTS[1], "monitor": OUTPUTS[0]}},
        "obs": None,
    }
    status.update(overrides)
    return status


class FakeLink:
    def __init__(self, on_state, on_status, on_result) -> None:
        self.on_state = on_state
        self.on_status = on_status
        self.on_result = on_result
        self.state = "stopped"
        self.owned = False
        self.posts: list[tuple[str, dict]] = []
        self.calls: list = []

    def attach_existing(self) -> bool:
        self.calls.append("attach")
        return False

    def start(self, arguments) -> None:
        self.calls.append(("start", list(arguments)))
        self.state = "starting"
        self.on_state("starting", "起動中")

    def stop(self, wait: bool = False) -> None:
        self.calls.append("stop")
        self.state = "stopping"
        self.on_state("stopping", "")

    def post(self, path: str, body: dict) -> None:
        self.posts.append((path, dict(body)))

    def close(self) -> None:
        self.calls.append("close")

    # 試験から状態を進める
    def become_running(self, status: dict) -> None:
        self.state = "running"
        self.owned = True
        self.on_state("running", "")
        self.on_status(status)

    def become_stopped(self) -> None:
        self.state = "stopped"
        self.owned = False
        self.on_state("stopped", "")


@unittest.skipIf(QApplication is None, "PySide6 が入っていない")
class MainWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from voicechanger.gui.main_window import MainWindow

        self.temporary = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temporary.name) / "settings.json"
        self.links: list[FakeLink] = []

        def link_factory(on_state, on_status, on_result):
            link = FakeLink(on_state, on_status, on_result)
            self.links.append(link)
            return link

        self.window = MainWindow(link_factory=link_factory,
                                 device_lister=lambda: {"inputs": INPUTS, "outputs": OUTPUTS},
                                 settings_factory=lambda: Settings(DEFAULT_CONFIG_PATH, self.settings_path),
                                 engine_arguments=["--no-obs"])
        self.link = self.links[0]
        self.wait_until(lambda: self.window._devices_ready)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.pump(0.05)
        self.temporary.cleanup()

    def pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def wait_until(self, condition, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("待ち合わせが時間切れ")
            self.app.processEvents()
            time.sleep(0.005)

    def test_stopped_window_disables_controls_and_shows_saved_values(self) -> None:
        window = self.window
        self.assertIn("attach", self.link.calls, "起動済みのエンジンに接続を試みる")
        self.assertEqual(window.start_button.text(), "▶ 開始")
        self.assertTrue(window.start_button.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in window.mode_buttons.values()))
        self.assertFalse(window.panels.isEnabled())
        self.assertEqual(window.input_combo.currentText(), INPUTS[0])
        self.assertEqual(window.output_combo.currentText(), OUTPUTS[0], "CABLE が無ければヘッドホン")
        self.assertEqual(window.semitones_slider.value(), 5.0, "保存値（既定）の表示")
        self.assertTrue(any("CABLE Input" in text for text in window.banners.texts()))

    def test_start_saves_devices_and_passes_arguments(self) -> None:
        window = self.window
        window.output_combo.setCurrentIndex(1)
        window.start_button.click()
        self.assertEqual(self.link.calls[-1], ("start", ["--no-obs"]))
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"]["output_device"], OUTPUTS[1])
        self.assertEqual(saved["devices"]["input_device"], INPUTS[0])
        self.assertEqual(window.start_button.text(), "■ 停止")

    def test_running_status_drives_the_view(self) -> None:
        window = self.window
        self.link.become_running(running_status(mode="ai"))
        self.assertTrue(window.mode_buttons["ai"].isChecked())
        self.assertTrue(window.mode_buttons["through"].isEnabled())
        self.assertEqual(window.panels.currentIndex(), 1, "AI のときは AI のパネル")
        self.assertEqual(window.voice_combo.currentText(), "声 B")
        self.assertEqual(window.ai_gain_slider.value(), 6.0)
        self.assertEqual(window.latency_label.text(), "遅延 78 ms")
        self.assertEqual(window.input_db_label.text(), "-20 dB")
        self.assertTrue(window.monitor_check.isEnabled())
        self.link.on_status(running_status(mode="through", through_remaining_s=12.2, monitor=True))
        texts = window.banners.texts()
        self.assertTrue(any("素の声" in text and "13 秒" in text for text in texts))
        self.assertTrue(any("モニターが有効" in text for text in texts))
        self.assertTrue(window.monitor_check.isChecked())

    def test_unavailable_mode_is_disabled_with_reason(self) -> None:
        self.link.become_running(running_status(available_modes=["dsp", "mute", "through"],
                                                unavailable={"ai": "VST が無い"}))
        self.assertFalse(self.window.mode_buttons["ai"].isEnabled())
        self.assertIn("VST が無い", self.window.mode_buttons["ai"].toolTip())

    def test_operations_post_to_the_api(self) -> None:
        window = self.window
        self.link.become_running(running_status())
        window.mode_buttons["ai"].click()
        self.assertEqual(self.link.posts[-1], ("/api/mode", {"mode": "ai"}))
        window.preset_buttons[2].click()
        self.assertEqual(self.link.posts[-1][0], "/api/dsp")
        self.assertIn("preset", self.link.posts[-1][1])
        window.voice_combo.setCurrentIndex(0)
        window.voice_combo.activated.emit(0)
        self.assertEqual(self.link.posts[-1], ("/api/ai", {"voice_index": 0}))
        window.monitor_check.click()
        self.assertEqual(self.link.posts[-1], ("/api/monitor", {"enabled": True}))

    def test_slider_sends_while_moving_and_is_not_overwritten(self) -> None:
        window = self.window
        self.link.become_running(running_status())
        slider = window.semitones_slider
        slider.slider.setValue(slider.slider.value() + 2)  # +1 半音
        self.wait_until(lambda: any(path == "/api/dsp" for path, _ in self.link.posts))
        self.assertEqual(self.link.posts[-1], ("/api/dsp", {"semitones": 6.0}))
        self.link.on_status(running_status())  # エンジンはまだ古い値（5.0）を返す
        self.assertEqual(slider.value(), 6.0, "操作の直後はエンジンの値で上書きしない")
        self.pump(0.9)
        self.link.on_status(running_status())
        self.assertEqual(slider.value(), 5.0, "時間が経てばエンジンの値に従う")

    def test_device_change_while_running_restarts_engine(self) -> None:
        window = self.window
        self.link.become_running(running_status())
        window.output_combo.setCurrentIndex(1)
        window.output_combo.activated.emit(1)
        self.assertEqual(self.link.calls[-1], "stop")
        self.link.become_stopped()
        self.wait_until(lambda: isinstance(self.link.calls[-1], tuple))
        self.assertEqual(self.link.calls[-1][0], "start")
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"]["output_device"], OUTPUTS[1], "止めてから保存し、起動し直す")

    def test_error_state_shows_reason_and_log_button(self) -> None:
        window = self.window
        window.start_button.click()
        self.link.state = "error"
        self.link.on_state("error", "output デバイス 'X' が見つかりません")
        self.assertTrue(window.log_button.isVisibleTo(window))
        self.assertTrue(any("見つかりません" in text for text in window.banners.texts()))
        self.assertEqual(window.start_button.text(), "▶ 開始")

    def test_warns_when_windows_default_playback_is_the_cable(self) -> None:
        window = self.window
        cable = "CABLE Input (VB-Audio Virtual Cable)"
        window._on_devices_loaded({"inputs": INPUTS, "outputs": OUTPUTS + [cable], "default_output": cable})
        self.assertTrue(any("既定の再生デバイス" in text for text in window.banners.texts()))
        window._on_devices_loaded({"inputs": INPUTS, "outputs": OUTPUTS + [cable], "default_output": OUTPUTS[0]})
        self.assertFalse(any("既定の再生デバイス" in text for text in window.banners.texts()))

    def test_close_closes_the_link(self) -> None:
        self.window.close()
        self.assertIn("close", self.link.calls)


@unittest.skipIf(QApplication is None, "PySide6 が入っていない")
class IconTest(unittest.TestCase):
    def test_write_ico_has_multiple_sizes(self) -> None:
        import struct

        QApplication.instance() or QApplication([])
        from voicechanger.gui.app import write_ico

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.ico"
            write_ico(path, sizes=(16, 32, 256))
            data = path.read_bytes()
            reserved, kind, count = struct.unpack_from("<HHH", data, 0)
            self.assertEqual((reserved, kind, count), (0, 1, 3))
            width, height = struct.unpack_from("<BB", data, 6 + 16 * 2)
            self.assertEqual((width, height), (0, 0), "256 px は 0 で表す")
            size, offset = struct.unpack_from("<II", data, 6 + 8)
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
