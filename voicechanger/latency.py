"""遅延の測定。入力と出力の音量の包絡（対数エネルギー）の相互相関で、出力の遅れを求める。

ピッチや声質を変えても発話の強弱のリズムは保たれるので、どのモードにも使える。
分解能は hop_ms（既定 1ms）。
"""
from __future__ import annotations

import numpy as np


def energy_envelope(signal: np.ndarray, sample_rate: int, hop_ms: float = 1.0, window_ms: float = 5.0) -> np.ndarray:
    hop = max(1, int(sample_rate * hop_ms / 1000))
    window = max(hop, int(sample_rate * window_ms / 1000))
    squared = np.square(signal.astype(np.float64))
    cumulative = np.concatenate([[0.0], np.cumsum(squared)])
    starts = np.arange(0, max(1, len(signal) - window), hop)
    energy = (cumulative[starts + window] - cumulative[starts]) / window
    return np.log10(energy + 1e-10)


def estimate_lag_samples(reference: np.ndarray, delayed: np.ndarray, sample_rate: int,
                         max_lag_ms: float = 400.0, hop_ms: float = 1.0) -> int:
    """delayed が reference より何サンプル遅れているか（負なら進んでいる）。"""
    reference_envelope = energy_envelope(reference, sample_rate, hop_ms)
    delayed_envelope = energy_envelope(delayed, sample_rate, hop_ms)
    length = min(len(reference_envelope), len(delayed_envelope))
    reference_envelope = reference_envelope[:length] - reference_envelope[:length].mean()
    delayed_envelope = delayed_envelope[:length] - delayed_envelope[:length].mean()
    max_lag = int(max_lag_ms / hop_ms)
    best_lag = 0
    best_score = -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            score = float(np.dot(reference_envelope[:length - lag], delayed_envelope[lag:]))
        else:
            score = float(np.dot(reference_envelope[-lag:], delayed_envelope[:length + lag]))
        if score > best_score:
            best_score = score
            best_lag = lag
    return int(round(best_lag * sample_rate * hop_ms / 1000))
