"""ピッチ・フォルマント変換（dsp モード）。

Rubber Band（pylibrb）の実時間モードを使う。エンジン R3（ENGINE_FINER）＋短い窓（WINDOW_SHORT）。
2026-11-17 の実測（Ryzen 9 3900X、48kHz、10ms ブロック）: 実時間比 約 0.02、1 ブロックの最悪 約 1.4ms。

フォルマントの指定:
  利用者向けの formant_factor は「元の声に対するフォルマントの倍率」（1.0 で元のまま）。
  Rubber Band の formant_scale は「ピッチで動いた後の包絡に掛ける倍率」（自動値は 1 / pitch_scale）なので、
  formant_scale = formant_factor / pitch_scale に換算する。formant_scale は R3 と FORMANT_PRESERVED のときだけ効く。
"""
from __future__ import annotations

import numpy as np

from .ringbuffer import AudioRingBuffer

try:  # pylibrb は任意の依存（GPLv2）
    import pylibrb
except ImportError:  # pragma: no cover - 入っていない環境向け
    pylibrb = None

# ±12 半音を超えると（-24 半音で実測）溜め量 256 でも取りこぼしが出たので、範囲を ±12 に限る
SEMITONE_LIMIT = 12.0
FORMANT_FACTOR_MIN = 0.5
FORMANT_FACTOR_MAX = 2.0
DEFAULT_PRIME_MARGIN = 256


class DspUnavailableError(RuntimeError):
    """pylibrb が使えない。"""


def is_available() -> bool:
    return pylibrb is not None


def clamp_params(semitones: float, formant_factor: float) -> tuple[float, float]:
    semitones = float(np.clip(semitones, -SEMITONE_LIMIT, SEMITONE_LIMIT))
    formant_factor = float(np.clip(formant_factor, FORMANT_FACTOR_MIN, FORMANT_FACTOR_MAX))
    return semitones, formant_factor


class PitchFormantShifter:
    """10ms などの固定長ブロックを受け、同じ長さのブロックを返すピッチ・フォルマント変換。

    Rubber Band は出力を不定の大きさで返すので、内部の FIFO に溜めて固定長で取り出す。
    溜まるまで（起動直後・取りこぼし直後）は無音を返す。
    """

    name = "dsp"

    def __init__(self, sample_rate: int, block_size: int, semitones: float = 0.0, formant_factor: float = 1.0,
                 prime_margin: int = DEFAULT_PRIME_MARGIN) -> None:
        if pylibrb is None:
            raise DspUnavailableError("pylibrb が入っていません（requirements-optional.txt）")
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._prime_margin = prime_margin
        self._prime_level = block_size + prime_margin
        options = (pylibrb.Option.PROCESS_REALTIME | pylibrb.Option.ENGINE_FINER | pylibrb.Option.WINDOW_SHORT |
                   pylibrb.Option.FORMANT_PRESERVED | pylibrb.Option.PitchHighConsistency |
                   pylibrb.Option.THREADING_NEVER)
        self._stretcher = pylibrb.RubberBandStretcher(sample_rate, 1, options)
        self._stretcher.set_max_process_size(block_size)
        self._fifo = AudioRingBuffer(sample_rate)  # 1 秒あれば十分
        self._primed = False
        self.underruns = 0
        self.semitones = 0.0
        self.formant_factor = 1.0
        self.set_params(semitones, formant_factor)

    def set_params(self, semitones: float, formant_factor: float) -> None:
        """動作中に変えてよい（ブロックの間で呼ぶ）。"""
        semitones, formant_factor = clamp_params(semitones, formant_factor)
        pitch_scale = 2.0 ** (semitones / 12.0)
        self._stretcher.pitch_scale = pitch_scale
        self._stretcher.formant_scale = formant_factor / pitch_scale
        self.semitones = semitones
        self.formant_factor = formant_factor

    def reset(self) -> None:
        self._stretcher.reset()
        self._fifo.clear()
        self._primed = False

    @property
    def latency_samples(self) -> int:
        """入力から出力までの遅延の見積（サンプル）。tests/test_dsp_shifter.py で実測と照合している。"""
        return estimate_latency_samples(self.semitones, self._prime_margin)

    @property
    def max_latency_samples(self) -> int:
        """設定できる範囲での遅延の最大（全モードの遅延をそろえる目標に使う）。"""
        return max(estimate_latency_samples(-SEMITONE_LIMIT, self._prime_margin),
                   estimate_latency_samples(SEMITONE_LIMIT, self._prime_margin))

    def describe(self) -> dict:
        return {"semitones": self.semitones, "formant_factor": self.formant_factor,
                "latency_ms": self.latency_samples * 1000.0 / self.sample_rate, "underruns": self.underruns}

    def process(self, block: np.ndarray) -> np.ndarray:
        if len(block) != self.block_size:
            raise ValueError(f"ブロック長は {self.block_size}（受け取ったのは {len(block)}）")
        self._stretcher.process(np.ascontiguousarray(block, dtype=np.float32).reshape(1, -1))
        available = self._stretcher.available()
        if available > 0:
            self._fifo.write(self._stretcher.retrieve(available)[0])
        output = np.zeros(self.block_size, dtype=np.float32)
        if not self._primed:
            if self._fifo.level < self._prime_level:
                return output
            self._primed = True
        count = self._fifo.read_into(output)
        if count < self.block_size:
            # 取りこぼし。足りない分は無音にし、もう一度溜め直す
            output[count:] = 0.0
            self.underruns += 1
            self._primed = False
        return output


# 実測した遅延（サンプル）。条件: 48kHz、ブロック 480、prime_margin 256、自分の声 30 秒、
# voicechanger.latency.estimate_lag_samples（包絡の相互相関、分解能 1ms）。2026-11-17 測定。
# 中間の値は線形補間する。tests/test_dsp_shifter.py が実測との差を確かめる。
_MEASURED_LATENCY_48K_MARGIN256 = {
    -12.0: 3744, -7.0: 3312, -4.0: 3120, 0.0: 2880, 4.0: 2640, 7.0: 2496, 12.0: 2352,
}


def estimate_latency_samples(semitones: float, prime_margin: int = DEFAULT_PRIME_MARGIN) -> int:
    points = sorted(_MEASURED_LATENCY_48K_MARGIN256.items())
    keys = np.array([key for key, _ in points])
    values = np.array([value for _, value in points], dtype=np.float64)
    base = float(np.interp(semitones, keys, values))
    return int(round(base + (prime_margin - DEFAULT_PRIME_MARGIN)))
