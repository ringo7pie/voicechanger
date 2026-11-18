"""設定のデバイス名・起動の入口（__main__）・入出力の小さな判断の試験。"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from voicechanger.__main__ import CONTROL_PATH, is_running, pid_alive, watch_parent
from voicechanger.audio_io import should_open_monitor
from voicechanger.config import DEFAULT_CONFIG_PATH, REPO_ROOT, Settings
from voicechanger.engine import Engine

VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


class DeviceSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temporary.name) / "settings.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_devices_are_saved_and_overlay_audio(self) -> None:
        settings = Settings(DEFAULT_CONFIG_PATH, self.settings_path)
        default_output = settings.audio["output_device"]
        settings.set_devices(input_device="マイク X", output_device="ヘッドホン Y")
        self.assertEqual(settings.audio["output_device"], "ヘッドホン Y")
        settings.save()
        restored = Settings(DEFAULT_CONFIG_PATH, self.settings_path)
        self.assertEqual(restored.audio["input_device"], "マイク X")
        self.assertEqual(restored.audio["output_device"], "ヘッドホン Y")
        self.assertEqual(restored.saved_devices, {"input_device": "マイク X", "output_device": "ヘッドホン Y"})
        restored.set_devices(output_device=None)
        self.assertEqual(restored.audio["output_device"], default_output, "選んでいない項目は既定に戻る")
        with self.assertRaises(KeyError):
            restored.set_devices(speaker="z")

    def test_engine_persistence_keeps_devices(self) -> None:
        settings = Settings(DEFAULT_CONFIG_PATH, self.settings_path)
        settings.set_devices(output_device="ヘッドホン Y")
        settings.save()
        engine = Engine(Settings(DEFAULT_CONFIG_PATH, self.settings_path), {})
        result = {}

        def worker() -> None:
            result["value"] = engine.submit("set_output_gain", timeout=2.0, output_gain_db=3.0)

        thread = threading.Thread(target=worker)
        thread.start()
        while thread.is_alive():
            engine.drain_commands()
            time.sleep(0.001)
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"], {"output_device": "ヘッドホン Y"})
        self.assertEqual(saved["output_gain_db"], 3.0)

    def test_command_line_override_is_not_saved(self) -> None:
        settings = Settings(DEFAULT_CONFIG_PATH, self.settings_path)
        settings.audio["output_device"] = "この起動だけ"
        settings.save()
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"], {})


class SmallDecisionsTest(unittest.TestCase):
    def test_monitor_is_skipped_when_same_as_output(self) -> None:
        self.assertFalse(should_open_monitor("ヘッドホン (A)", "ヘッドホン (a)"))
        self.assertTrue(should_open_monitor("CABLE Input (VB-Audio Virtual Cable)", "ヘッドホン (A)"))
        self.assertFalse(should_open_monitor("ヘッドホン (A)", None))

    def test_pid_alive_and_parent_watch(self) -> None:
        import os

        self.assertTrue(pid_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(pid_alive(child.pid))
        stop = threading.Event()
        watch_parent(child.pid, stop, interval=0.05)
        self.assertTrue(stop.wait(2.0), "親が居なければすぐ止まる")


@unittest.skipUnless(VENV_PYTHON.is_file(), ".venv が無い")
class EntryPointTest(unittest.TestCase):
    def run_module(self, *arguments: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
        return subprocess.run([str(VENV_PYTHON), "-m", "voicechanger", *arguments], cwd=REPO_ROOT,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)

    def test_devices_json(self) -> None:
        completed = self.run_module("devices", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(document["host_api"], "Windows WASAPI")
        self.assertIsInstance(document["inputs"], list)
        self.assertGreater(len(document["outputs"]), 0)
        self.assertIn("default_output", document)
        if document["default_output"] is not None:
            self.assertIn(document["default_output"], document["outputs"])

    def test_missing_device_fails_with_one_line_and_no_control_file(self) -> None:
        if is_running() is not None:
            self.skipTest("エンジンが起動中")
        wav = REPO_ROOT / "tests" / "_silence.wav"
        completed = self.run_module("run", "--no-obs", "--no-monitor", "--input-wav", str(wav),
                                    "--output-device", "存在しないデバイス 12345")
        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        self.assertIn("VOICECHANGER_DEVICE_ERROR", completed.stdout)
        self.assertFalse(CONTROL_PATH.exists(), "失敗したら control.json を残さない")

    def test_dead_parent_is_refused(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        completed = self.run_module("run", "--no-obs", "--parent-pid", str(child.pid))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("VOICECHANGER_START_FAILED", completed.stdout)


if __name__ == "__main__":
    unittest.main()
