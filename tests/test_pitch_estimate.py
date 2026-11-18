import unittest

import numpy as np

from tests.signals import harmonic_vowel
from voicechanger.pitch_estimate import estimate_median_f0, hz_to_midi


class PitchEstimateTest(unittest.TestCase):
    def test_synthetic_vowels(self) -> None:
        for sample_rate in (16000, 48000):
            for f0 in (85.0, 110.0, 150.0, 220.0, 330.0):
                with self.subTest(sample_rate=sample_rate, f0=f0):
                    median, voiced = estimate_median_f0(harmonic_vowel(1.0, f0, sample_rate), sample_rate)
                    self.assertGreater(voiced, 50)
                    self.assertAlmostEqual(median / f0, 1.0, delta=0.01)

    def test_silence_and_noise_are_unvoiced(self) -> None:
        generator = np.random.default_rng(0)
        self.assertEqual(estimate_median_f0(np.zeros(16000), 16000), (0.0, 0))
        noise = (0.1 * generator.standard_normal(16000)).astype(np.float32)
        _, voiced = estimate_median_f0(noise, 16000)
        self.assertLess(voiced, 10)

    def test_hz_to_midi(self) -> None:
        self.assertAlmostEqual(hz_to_midi(440.0), 69.0)
        self.assertAlmostEqual(hz_to_midi(220.0), 57.0)


if __name__ == "__main__":
    unittest.main()
