"""声の高さの推定。Beatrice の Average Source Pitch（MIDI ノート番号の単位）の既定値を決めるのに使う。

フレームごとに YIN（累積平均で正規化した差分関数）で基本周波数を求め、有声のフレームの中央値を返す。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def hz_to_midi(frequency: float) -> float:
    return 69.0 + 12.0 * math.log2(frequency / 440.0)


def yin_frame(frame: np.ndarray, sample_rate: int, fmin: float, fmax: float,
              threshold: float = 0.15) -> tuple[float, float]:
    """(基本周波数 Hz, 周期性 0〜1)。周期性は 1 - 差分関数の最小値。"""
    signal = frame.astype(np.float64)
    tau_min = max(2, int(sample_rate / fmax))
    tau_max = int(sample_rate / fmin)
    window = len(signal) - tau_max
    if window <= tau_max // 2:
        raise ValueError("フレームが短すぎる（最低周波数の周期の 1.5 倍以上にする）")
    head = signal[:window]
    size = 1 << int(math.ceil(math.log2(len(signal) + window)))
    cross = np.fft.irfft(np.fft.rfft(signal, size) * np.conj(np.fft.rfft(head, size)), size)[:tau_max + 1]
    squares = np.concatenate([[0.0], np.cumsum(signal * signal)])
    taus = np.arange(tau_max + 1)
    head_energy = squares[window] - squares[0]
    shifted_energy = squares[taus + window] - squares[taus]
    difference = head_energy + shifted_energy - 2.0 * cross
    difference[0] = 0.0
    cumulative = np.cumsum(difference[1:])
    normalized = np.ones(tau_max + 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        normalized[1:] = np.where(cumulative > 0, difference[1:] * taus[1:] / cumulative, 1.0)
    candidates = np.nonzero(normalized[tau_min:tau_max] < threshold)[0]
    if len(candidates) > 0:
        tau = tau_min + int(candidates[0])
        while tau + 1 < tau_max and normalized[tau + 1] < normalized[tau]:
            tau += 1  # しきい値を下回った谷の底まで進む
    else:
        tau = tau_min + int(np.argmin(normalized[tau_min:tau_max]))
    periodicity = float(1.0 - normalized[tau])
    if 1 <= tau < tau_max:
        left, center, right = normalized[tau - 1], normalized[tau], normalized[tau + 1]
        denominator = left - 2 * center + right
        offset = 0.5 * (left - right) / denominator if denominator != 0 else 0.0
    else:
        offset = 0.0
    return sample_rate / (tau + offset), periodicity


def estimate_median_f0(signal: np.ndarray, sample_rate: int, fmin: float = 60.0, fmax: float = 500.0,
                       frame_ms: float = 50.0, hop_ms: float = 10.0, min_periodicity: float = 0.75,
                       min_level_db: float = -45.0) -> tuple[float, int]:
    """(中央値の基本周波数 Hz, 有声フレーム数)。有声が無ければ (0.0, 0)。"""
    frame = int(sample_rate * frame_ms / 1000)
    hop = int(sample_rate * hop_ms / 1000)
    values = []
    for start in range(0, len(signal) - frame, hop):
        piece = signal[start:start + frame]
        level_db = 20 * math.log10(float(np.sqrt(np.mean(np.square(piece.astype(np.float64))))) + 1e-12)
        if level_db < min_level_db:
            continue
        frequency, periodicity = yin_frame(piece, sample_rate, fmin, fmax)
        if periodicity >= min_periodicity and fmin <= frequency <= fmax:
            values.append(frequency)
    if not values:
        return 0.0, 0
    return float(np.median(values)), len(values)


def estimate_average_source_pitch(wav_paths: list[Path], sample_rate: int = 16000) -> dict:
    """WAV 群から自分の声の高さ（MIDI ノート番号）を求める。"""
    from pedalboard.io import AudioFile

    frequencies = []
    for wav_path in wav_paths:
        with AudioFile(str(wav_path)).resampled_to(sample_rate) as audio_file:
            audio = audio_file.read(audio_file.frames).mean(axis=0)
        median, voiced = estimate_median_f0(audio, sample_rate)
        if voiced > 0:
            frequencies.extend([median] * voiced)
    if not frequencies:
        raise ValueError("有声の区間が見つかりませんでした")
    median_hz = float(np.median(frequencies))
    return {"median_hz": median_hz, "midi": hz_to_midi(median_hz), "voiced_frames": len(frequencies),
            "files": len(wav_paths)}
