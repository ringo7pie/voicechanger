import unittest

import numpy as np

from tests.signals import (SAMPLE_RATE, estimate_f0, estimate_formant_scale, harmonic_vowel, run_blocks,
                           syllable_signal)
from voicechanger import BLOCK_SIZE
from voicechanger.dsp_shifter import SEMITONE_LIMIT, PitchFormantShifter, is_available
from voicechanger.latency import estimate_lag_samples


@unittest.skipUnless(is_available(), "pylibrb が入っていない")
class PitchFormantShifterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vowel = harmonic_vowel(2.0, 150.0)

    def shift(self, semitones: float, formant_factor: float) -> np.ndarray:
        shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE, semitones, formant_factor)
        output = run_blocks(shifter, self.vowel, BLOCK_SIZE)
        self.assertEqual(shifter.underruns, 0)
        return output[SAMPLE_RATE // 2:]  # 立ち上がりを除く

    def test_pitch_ratio_follows_semitones(self) -> None:
        for semitones in (5.0, -4.0, 12.0):
            with self.subTest(semitones=semitones):
                expected = 150.0 * 2.0 ** (semitones / 12.0)
                measured = estimate_f0(self.shift(semitones, 1.0)[:SAMPLE_RATE // 2])
                self.assertAlmostEqual(measured / expected, 1.0, delta=0.015)

    def test_formants_are_preserved_when_factor_is_one(self) -> None:
        for semitones in (5.0, -4.0):
            with self.subTest(semitones=semitones):
                shifted_f0 = 150.0 * 2.0 ** (semitones / 12.0)
                scale = estimate_formant_scale(self.vowel, 150.0, self.shift(semitones, 1.0), shifted_f0)
                self.assertAlmostEqual(scale, 1.0, delta=0.08)

    def test_formant_factor_moves_envelope(self) -> None:
        for semitones, factor in ((0.0, 1.25), (0.0, 0.8), (5.0, 1.2)):
            with self.subTest(semitones=semitones, factor=factor):
                shifted_f0 = 150.0 * 2.0 ** (semitones / 12.0)
                scale = estimate_formant_scale(self.vowel, 150.0, self.shift(semitones, factor), shifted_f0)
                self.assertAlmostEqual(scale, factor, delta=0.1)

    def test_blocks_keep_length_and_stay_finite(self) -> None:
        shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE, 7.0, 1.1)
        for start in range(0, len(self.vowel) - BLOCK_SIZE, BLOCK_SIZE):
            block = shifter.process(self.vowel[start:start + BLOCK_SIZE])
            self.assertEqual(block.shape, (BLOCK_SIZE,))
            self.assertEqual(block.dtype, np.float32)
            self.assertTrue(np.all(np.isfinite(block)))

    def test_silence_stays_silent(self) -> None:
        shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE, 5.0, 1.2)
        output = run_blocks(shifter, np.zeros(SAMPLE_RATE, dtype=np.float32), BLOCK_SIZE)
        self.assertLess(float(np.max(np.abs(output))), 1e-6)

    def test_wrong_block_length_is_rejected(self) -> None:
        shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE)
        with self.assertRaises(ValueError):
            shifter.process(np.zeros(BLOCK_SIZE + 1, dtype=np.float32))

    def test_params_are_clamped(self) -> None:
        shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE, 40.0, 9.0)
        self.assertEqual(shifter.semitones, SEMITONE_LIMIT)
        self.assertEqual(shifter.formant_factor, 2.0)

    def test_latency_estimate_matches_measurement(self) -> None:
        signal = syllable_signal(8.0)
        for semitones in (-7.0, 0.0, 7.0):
            with self.subTest(semitones=semitones):
                shifter = PitchFormantShifter(SAMPLE_RATE, BLOCK_SIZE, semitones, 1.0)
                output = run_blocks(shifter, signal, BLOCK_SIZE)
                measured = estimate_lag_samples(signal[:len(output)], output, SAMPLE_RATE)
                self.assertLess(abs(measured - shifter.latency_samples), BLOCK_SIZE,
                                f"実測 {measured} / 見積 {shifter.latency_samples}")


if __name__ == "__main__":
    unittest.main()
