import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from voicechanger.config import DEFAULT_CONFIG_PATH, Settings
from voicechanger.engine import DelayLine, Engine, Limiter, ModeUnavailableError, RecordingGuardError

BLOCK = 480


class FakeProcessor:
    """一定値か、遅延した入力を返す偽の処理器。"""

    def __init__(self, name: str, latency: int = 0, value: float | None = None) -> None:
        self.name = name
        self.latency_samples = latency
        self.value = value
        self.fail = False
        self.resets = 0
        self._delay = DelayLine(latency)

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.fail:
            raise RuntimeError("わざと失敗")
        if self.value is not None:
            return np.full(len(block), self.value, dtype=np.float32)
        return self._delay.process(np.array(block, dtype=np.float32))

    def reset(self) -> None:
        self.resets += 1
        self._delay.reset()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class EngineTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temporary.name) / "settings.json"
        self.clock = FakeClock()
        self.recording = False

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_engine(self, processors: dict, mode: str = "dsp", unavailable: dict | None = None) -> Engine:
        self.settings_path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        settings = Settings(DEFAULT_CONFIG_PATH, self.settings_path)
        return Engine(settings, processors, unavailable=unavailable, recording_guard=lambda: self.recording,
                      clock=self.clock)

    @staticmethod
    def run_blocks(engine: Engine, count: int, value: float = 0.0) -> list[np.ndarray]:
        outputs = []
        for _ in range(count):
            engine.drain_commands()
            outputs.append(engine.process_block(np.full(BLOCK, value, dtype=np.float32)))
        return outputs

    @staticmethod
    def command(engine: Engine, name: str, **arguments):
        """処理ループを回さずに命令を実行する（drain_commands を自分で呼ぶ）。"""
        result = {}

        def worker() -> None:
            try:
                result["value"] = engine.submit(name, timeout=2.0, **arguments)
            except BaseException as error:  # noqa: BLE001
                result["error"] = error

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 2.0
        while thread.is_alive() and time.monotonic() < deadline:
            engine.drain_commands()
            time.sleep(0.001)
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")


class FailSafeTest(EngineTestBase):
    def test_exception_outputs_silence_and_falls_back_to_mute(self) -> None:
        dsp = FakeProcessor("dsp", value=0.3)
        engine = self.make_engine({"dsp": dsp})
        self.run_blocks(engine, 5)
        dsp.fail = True
        output = self.run_blocks(engine, 1, value=0.5)[0]
        self.assertTrue(np.all(output == 0.0), "例外のときは素の声ではなく無音")
        engine.refresh_status()
        status = engine.status()
        self.assertEqual(status["mode"], "mute")
        self.assertEqual(status["buffers"]["processor_errors"], 1)
        self.assertIn("エラー", status["notice"])
        self.assertTrue(np.all(self.run_blocks(engine, 3, value=0.5)[-1] == 0.0))

    def test_failure_of_switch_target_keeps_current_mode(self) -> None:
        dsp = FakeProcessor("dsp", value=0.1)
        ai = FakeProcessor("ai", value=0.2)
        ai.fail = True
        engine = self.make_engine({"dsp": dsp, "ai": ai})
        self.run_blocks(engine, 2)
        self.command(engine, "set_mode", mode="ai")
        outputs = self.run_blocks(engine, 3)
        engine.refresh_status()
        self.assertEqual(engine.status()["mode"], "dsp")
        self.assertAlmostEqual(float(outputs[-1][-1]), 0.1, places=5)

    def test_startup_mode_falls_back_when_unavailable(self) -> None:
        engine = self.make_engine({}, mode="ai", unavailable={"ai": "VST が無い"})
        engine.refresh_status()
        self.assertEqual(engine.status()["mode"], "mute")
        self.assertIn("VST が無い", engine.status()["notice"])
        with self.assertRaises(ModeUnavailableError):
            self.command(engine, "set_mode", mode="ai")

    def test_saved_through_is_never_restored(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)}, mode="through")
        engine.refresh_status()
        self.assertEqual(engine.status()["mode"], "mute")


class ThroughAndMonitorTest(EngineTestBase):
    def test_through_is_not_persisted(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        self.command(engine, "set_mode", mode="through")
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mode"], "dsp")
        self.command(engine, "set_mode", mode="mute")
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mode"], "mute")

    def test_through_times_out_to_mute(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        self.command(engine, "set_mode", mode="through")
        self.run_blocks(engine, 20, value=0.2)
        engine.refresh_status()
        self.assertEqual(engine.status()["mode"], "through")
        self.clock.now += 61.0
        outputs = self.run_blocks(engine, 3, value=0.2)
        engine.refresh_status()
        self.assertEqual(engine.status()["mode"], "mute")
        self.assertTrue(np.all(outputs[-1] == 0.0))
        self.assertIn("無音に戻しました", engine.status()["notice"])

    def test_through_and_monitor_rejected_while_recording(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        self.recording = True
        with self.assertRaises(RecordingGuardError):
            self.command(engine, "set_mode", mode="through")
        with self.assertRaises(RecordingGuardError):
            self.command(engine, "set_monitor", enabled=True)

    def test_recording_start_stops_through_and_monitor(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        self.command(engine, "set_mode", mode="through")
        self.command(engine, "set_monitor", enabled=True)
        self.run_blocks(engine, 15, value=0.2)
        self.command(engine, "recording_changed", recording=True)
        outputs = self.run_blocks(engine, 2, value=0.2)
        engine.refresh_status()
        status = engine.status()
        self.assertEqual(status["mode"], "mute")
        self.assertFalse(status["monitor"])
        self.assertTrue(np.all(outputs[-1] == 0.0))


class LatencyAndSwitchingTest(EngineTestBase):
    def test_all_modes_share_the_aligned_latency(self) -> None:
        dsp = FakeProcessor("dsp", latency=1000)
        ai = FakeProcessor("ai", latency=300)
        engine = self.make_engine({"dsp": dsp, "ai": ai})
        self.assertEqual(engine.aligned_latency_samples, 1000)
        for mode in ("dsp", "ai", "through"):
            with self.subTest(mode=mode):
                self.command(engine, "set_mode", mode=mode)
                self.run_blocks(engine, 30)  # 温めと切り替えを終わらせ、無音で満たす
                impulse = np.zeros(BLOCK, dtype=np.float32)
                impulse[10] = 0.5
                engine.drain_commands()
                outputs = [engine.process_block(impulse)] + self.run_blocks(engine, 4)
                joined = np.concatenate(outputs)
                self.assertEqual(int(np.argmax(np.abs(joined))), 10 + 1000)

    def test_switch_warms_up_then_crossfades(self) -> None:
        dsp = FakeProcessor("dsp", value=0.1)
        ai = FakeProcessor("ai", value=0.2)
        engine = self.make_engine({"dsp": dsp, "ai": ai})
        engine.settings.safety["align_latency"] = False
        self.run_blocks(engine, 3)
        resets_before = ai.resets
        self.command(engine, "set_mode", mode="ai")
        self.assertEqual(ai.resets, resets_before + 1, "切り替え先はリセットしてから温める")
        outputs = self.run_blocks(engine, 15)
        warmup = engine._warmup_blocks
        self.assertEqual(warmup, 10)
        for index in range(warmup):
            self.assertTrue(np.allclose(outputs[index], 0.1), f"温め中 {index} は元の声のまま")
        crossfade = outputs[warmup]
        self.assertAlmostEqual(float(crossfade[0]), 0.1, places=4)
        self.assertAlmostEqual(float(crossfade[-1]), 0.2, places=4)
        self.assertTrue(np.all(np.diff(crossfade) >= -1e-7), "クロスフェードは単調")
        self.assertTrue(np.allclose(outputs[warmup + 1], 0.2))

    def test_switch_to_mute_takes_one_block(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        self.run_blocks(engine, 3)
        self.command(engine, "set_mode", mode="mute")
        outputs = self.run_blocks(engine, 2)
        self.assertAlmostEqual(float(outputs[0][-1]), 0.0, places=5)
        self.assertTrue(np.all(outputs[1] == 0.0))


class DriftAndLimiterTest(EngineTestBase):
    def test_drift_is_corrected_only_in_quiet_blocks(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.0)})
        prefill = engine.output_ring.level
        engine.output_ring.write(np.zeros(int(0.05 * 48000), dtype=np.float32))  # 50ms 溜まりすぎ
        loud = np.full(BLOCK, 0.3, dtype=np.float32)
        engine.emit(loud)
        self.assertEqual(engine.counters["drift_drops"], 0, "声のあるブロックでは捨てない")
        engine.emit(np.zeros(BLOCK, dtype=np.float32))
        self.assertEqual(engine.counters["drift_drops"], 1)
        engine.output_ring.clear()
        engine.emit(np.zeros(BLOCK, dtype=np.float32))
        self.assertEqual(engine.counters["drift_inserts"], 1)
        engine.output_ring.clear()
        engine.output_ring.write(np.zeros(prefill + int(0.2 * 48000), dtype=np.float32))
        engine.emit(loud)
        self.assertEqual(engine.counters["forced_drops"], 1, "上限を超えたら声があっても戻す")

    def test_limiter_caps_peak_and_removes_nonfinite(self) -> None:
        limiter = Limiter(-1.0, 10.0)
        block = np.full(BLOCK, 2.0, dtype=np.float32)
        block[5] = np.nan
        block[6] = np.inf
        output = limiter.process(block)
        self.assertTrue(np.all(np.isfinite(output)))
        self.assertLessEqual(float(np.max(np.abs(output))), 10 ** (-1 / 20) + 1e-6)
        self.assertEqual(limiter.nonfinite_blocks, 1)

    def test_output_gain_is_applied_and_persisted(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.1)})
        engine.settings.safety["align_latency"] = False
        self.command(engine, "set_output_gain", output_gain_db=6.0)
        output = self.run_blocks(engine, 3)[-1]
        self.assertAlmostEqual(float(output[-1]), 0.1 * 10 ** (6 / 20), places=3)
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["output_gain_db"], 6.0)


class LoopTest(EngineTestBase):
    def test_run_loop_processes_input_and_commands(self) -> None:
        engine = self.make_engine({"dsp": FakeProcessor("dsp", value=0.25)})
        engine.mark_outputs_started()
        stop = threading.Event()
        thread = threading.Thread(target=engine.run, args=(stop,))
        thread.start()
        try:
            for _ in range(30):
                engine.push_input(np.zeros(BLOCK, dtype=np.float32))
                time.sleep(0.002)
            result = engine.submit("set_mode", timeout=2.0, mode="mute")
            self.assertIn(result["mode"], ("dsp", "mute"))
            deadline = time.monotonic() + 1.0
            while engine.output_ring.level < BLOCK and time.monotonic() < deadline:
                time.sleep(0.005)
            pulled = engine.pull_output(BLOCK)
            self.assertEqual(len(pulled), BLOCK)
        finally:
            stop.set()
            thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreater(engine.counters["blocks"], 20)


if __name__ == "__main__":
    unittest.main()
