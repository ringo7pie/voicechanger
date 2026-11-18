"""動作確認用の合成音（実行時にも使うので tests/ ではなくパッケージに置く）。"""
from __future__ import annotations

import numpy as np

FORMANTS = ((700.0, 120.0), (1200.0, 150.0), (2600.0, 220.0))


def harmonic_vowel(seconds: float, f0: float, sample_rate: int, seed: int = 1, max_frequency: float = 7000.0) -> np.ndarray:
    """倍音の振幅をフォルマント包絡で決めた母音（-8 dBFS 程度）。"""
    generator = np.random.default_rng(seed)
    time_axis = np.arange(int(seconds * sample_rate)) / sample_rate
    signal = np.zeros_like(time_axis)
    frequencies = f0 * np.arange(1, int(max_frequency / f0) + 1)
    amplitudes = np.full_like(frequencies, 0.02)
    for center, bandwidth in FORMANTS:
        amplitudes += 1.0 / (1.0 + ((frequencies - center) / bandwidth) ** 2)
    for frequency, amplitude in zip(frequencies, amplitudes):
        signal += amplitude * np.sin(2 * np.pi * frequency * time_axis + generator.uniform(0, 2 * np.pi))
    signal /= np.max(np.abs(signal))
    return (0.4 * signal).astype(np.float32)
