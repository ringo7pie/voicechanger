"""試験用の合成信号と、ピッチ・フォルマントの測定。"""
from __future__ import annotations

import numpy as np

from voicechanger import tones

SAMPLE_RATE = 48000


def harmonic_vowel(seconds: float, f0: float, sample_rate: int = SAMPLE_RATE, seed: int = 1) -> np.ndarray:
    """倍音の振幅をフォルマント包絡で決めた母音。包絡が既知なので、フォルマントの移動を測れる。"""
    return tones.harmonic_vowel(seconds, f0, sample_rate, seed=seed)


def syllable_signal(seconds: float, sample_rate: int = SAMPLE_RATE, seed: int = 3) -> np.ndarray:
    """母音を 80〜300ms の音節と 40〜200ms の無音で断続させた信号（遅延の測定用）。"""
    generator = np.random.default_rng(seed)
    total = int(seconds * sample_rate)
    output = np.zeros(total, dtype=np.float32)
    position = int(0.2 * sample_rate)
    while position < total:
        length = int(generator.uniform(0.08, 0.3) * sample_rate)
        f0 = generator.uniform(110, 180)
        piece = harmonic_vowel(length / sample_rate + 0.01, f0, sample_rate, seed=int(position))[:length]
        fade = min(len(piece) // 4, int(0.01 * sample_rate))
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            piece[:fade] *= ramp
            piece[-fade:] *= ramp[::-1]
        end = min(total, position + len(piece))
        output[position:end] = piece[:end - position] * generator.uniform(0.4, 1.0)
        position = end + int(generator.uniform(0.04, 0.2) * sample_rate)
    return output


def load_self_voice(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray | None:
    """設定の self_voice_dir にある自分の声を、合間に 250ms の無音を挟んでつなぐ。無ければ None。

    Beatrice は出力の音量の出入りを入力に遅れなく合わせるため、音量が一定の合成音では
    中身の遅れ（約 36ms）を測れない。遅延の試験には実声を使う。
    """
    from voicechanger.config import Settings

    voice_dir = Settings(settings_path=None).self_voice_dir
    if voice_dir is None or not voice_dir.is_dir():
        return None
    from pedalboard.io import AudioFile

    pieces = []
    total = 0
    for wav_path in sorted(voice_dir.glob("*.wav")):
        with AudioFile(str(wav_path)).resampled_to(sample_rate) as audio_file:
            piece = audio_file.read(audio_file.frames).mean(axis=0).astype(np.float32)
        pieces += [piece, np.zeros(sample_rate // 4, dtype=np.float32)]
        total += len(piece) + sample_rate // 4
        if total >= seconds * sample_rate:
            break
    return np.concatenate(pieces) if pieces else None


def estimate_f0(signal: np.ndarray, sample_rate: int = SAMPLE_RATE, fmin: float = 60.0, fmax: float = 600.0) -> float:
    """自己相関による基本周波数の推定（定常な合成音向け）。"""
    frame = signal.astype(np.float64) - float(np.mean(signal))
    spectrum = np.fft.rfft(frame, n=2 * len(frame))
    autocorrelation = np.fft.irfft(np.abs(spectrum) ** 2)[:len(frame)]
    lag_min = int(sample_rate / fmax)
    lag_max = int(sample_rate / fmin)
    best_lag = lag_min + int(np.argmax(autocorrelation[lag_min:lag_max]))
    left, center, right = autocorrelation[best_lag - 1], autocorrelation[best_lag], autocorrelation[best_lag + 1]
    denominator = left - 2 * center + right
    offset = 0.5 * (left - right) / denominator if denominator != 0 else 0.0
    return sample_rate / (best_lag + offset)


def log_envelope(signal: np.ndarray, f0: float, sample_rate: int = SAMPLE_RATE) -> tuple[np.ndarray, np.ndarray]:
    """ケプストラムで平滑化した対数スペクトル包絡（周波数, 値）。"""
    frame_size = 4096
    window = np.hanning(frame_size)
    spectra = [np.abs(np.fft.rfft(signal[start:start + frame_size] * window)) ** 2
               for start in range(0, len(signal) - frame_size, frame_size // 2)]
    log_power = np.log(np.mean(spectra, axis=0) + 1e-12)
    cepstrum = np.fft.irfft(log_power)
    lifter = int(0.5 * sample_rate / f0)
    cepstrum[lifter:-lifter] = 0.0
    return np.fft.rfftfreq(frame_size, 1 / sample_rate), np.fft.rfft(cepstrum).real


def estimate_formant_scale(reference: np.ndarray, reference_f0: float, shifted: np.ndarray, shifted_f0: float,
                           sample_rate: int = SAMPLE_RATE) -> float:
    """shifted の包絡が reference の包絡を何倍の周波数へ動かしたものかを推定する。"""
    frequencies, reference_envelope = log_envelope(reference, reference_f0, sample_rate)
    _, shifted_envelope = log_envelope(shifted, shifted_f0, sample_rate)
    log_axis = np.exp(np.linspace(np.log(300.0), np.log(3500.0), 400))
    reference_curve = np.interp(log_axis, frequencies, reference_envelope)
    best_scale = 1.0
    best_score = -np.inf
    for scale in np.linspace(0.6, 1.7, 221):
        shifted_curve = np.interp(log_axis * scale, frequencies, shifted_envelope)
        score = float(np.corrcoef(reference_curve, shifted_curve)[0, 1])
        if score > best_score:
            best_score = score
            best_scale = float(scale)
    return best_scale


def run_blocks(processor, signal: np.ndarray, block_size: int) -> np.ndarray:
    usable = (len(signal) // block_size) * block_size
    output = np.empty(usable, dtype=np.float32)
    for start in range(0, usable, block_size):
        output[start:start + block_size] = processor.process(signal[start:start + block_size])
    return output
