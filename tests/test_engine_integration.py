"""実物の処理器（Rubber Band と Beatrice）を載せたエンジンを、デバイス無しで回す結合試験。"""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.signals import SAMPLE_RATE, load_self_voice, syllable_signal
from tests.test_beatrice_host import RUNTIME_READY
from voicechanger import BLOCK_SIZE
from voicechanger.config import DEFAULT_CONFIG_PATH, Settings
from voicechanger.dsp_shifter import is_available as dsp_available
from voicechanger.engine import Engine, build_processors
from voicechanger.latency import estimate_lag_samples


@unittest.skipUnless(RUNTIME_READY and dsp_available(), "Beatrice の runtime か pylibrb が無い")
class RealProcessorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        settings = Settings(DEFAULT_CONFIG_PATH, Path(cls.temporary.name) / "settings.json")
        processors, unavailable = build_processors(settings, self_pitch_midi=46.0)
        cls.assertEqual = unittest.TestCase.assertEqual
        if unavailable:
            raise unittest.SkipTest(f"処理器を作れない: {unavailable}")
        cls.engine = Engine(settings, processors)
        voice = load_self_voice(20.0)
        cls.has_voice = voice is not None
        cls.signal = voice if voice is not None else syllable_signal(6.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_mode(self, mode: str) -> np.ndarray:
        engine = self.engine
        engine._command_set_mode(mode)
        silence = np.zeros(BLOCK_SIZE, dtype=np.float32)
        for _ in range(40):  # 温めと切り替えを終える
            engine.process_block(silence)
        self.assertEqual(engine._active, mode)
        outputs = [engine.process_block(self.signal[start:start + BLOCK_SIZE])
                   for start in range(0, len(self.signal) - BLOCK_SIZE + 1, BLOCK_SIZE)]
        return np.concatenate(outputs)

    def test_every_mode_has_the_aligned_latency(self) -> None:
        if not self.has_voice:
            self.skipTest("自分の声の録音が無い。合成音では Beatrice の遅延を測れない")
        aligned = self.engine.aligned_latency_samples
        for mode in ("dsp", "ai", "through"):
            with self.subTest(mode=mode):
                output = self.run_mode(mode)
                self.assertGreater(float(np.max(np.abs(output))), 0.01)
                measured = estimate_lag_samples(self.signal[:len(output)], output, SAMPLE_RATE)
                self.assertLess(abs(measured - aligned), BLOCK_SIZE, f"{mode}: 実測 {measured} / 目標 {aligned}")

    def test_processing_is_fast_enough(self) -> None:
        for mode in ("dsp", "ai"):
            with self.subTest(mode=mode):
                self.engine._block_seconds_total = 0.0
                self.engine._block_seconds_worst = 0.0
                blocks_before = self.engine.counters["blocks"]
                self.run_mode(mode)
                blocks = self.engine.counters["blocks"] - blocks_before
                average_ms = self.engine._block_seconds_total * 1000 / blocks
                self.assertLess(average_ms, 5.0, "10ms のブロックを平均 5ms 未満で処理する")


if __name__ == "__main__":
    unittest.main()
