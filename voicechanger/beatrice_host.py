"""AI 変換（ai モード）: Beatrice 2 公式 VST3 を pedalboard で読み込んで使う。

公式 VST のソース（prj-beatrice/beatrice-vst v2.0.0-rc.3）と実測（2026-11-17）で確かめた事実:
- バンドルのフォルダ（*.vst3）を渡すと JUCE のスキャンが失敗する。バンドル内の DLL 本体
  （Contents/x86_64-win/*.vst3）を渡すと読み込める。
- モデルの指定は VST のパラメータではなく、状態（IComponent の state）の ID 1 の文字列（TOML の絶対パス）。
  pedalboard の raw_state は JUCE の形式（b"VC2!" + uint32 長さ + XML + NUL）で、XML の <IComponent> は
  JUCE 独自の base64（"<バイト数>." + 6bit ずつ下位から詰めた文字列）。
- 状態の並びは int32 サイズ + {int16 ID, int32 型(0:int, 1:double, 2:文字列), 値} の繰り返し。
- モデル未設定・読み込み失敗のときは無音を出す（素の声は出ない）。
- ホストから Voice を変えても Pitch Shift は自動では連動しない。
  GUI と同じ式 PitchShift = clamp(平均目標ピッチ[声] + FormantShift - AverageSourcePitch, ±24) をこちらで計算する。
- 状態の設定（モデルの読み込み）に約 1.2 秒かかる。処理ループの外で行う。
"""
from __future__ import annotations

import re
import struct
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

JUCE_BASE64_ALPHABET = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
MODEL_PARAMETER_ID = 1
PITCH_SHIFT_LIMIT = 24.0
FORMANT_SHIFT_LIMIT = 2.0


class BeatriceLoadError(RuntimeError):
    """VST やモデルを読み込めない、または読み込んでも音が出ない。"""


@dataclass(frozen=True)
class BeatriceVoice:
    index: int
    name: str
    average_pitch: float


# ---------------------------------------------------------------- 状態の組み立て
def juce_base64_decode(text: str) -> bytes:
    size_text, separator, payload = text.strip().partition(".")
    if separator != "." or not size_text.isdigit():
        raise ValueError("JUCE の base64 ではない")
    size = int(size_text)
    value = 0
    for index, character in enumerate(payload):
        value |= JUCE_BASE64_ALPHABET.index(character) << (6 * index)
    return value.to_bytes(size + 1, "little")[:size]


def juce_base64_encode(data: bytes) -> str:
    value = int.from_bytes(data, "little")
    char_count = (len(data) * 8 + 5) // 6
    return f"{len(data)}." + "".join(JUCE_BASE64_ALPHABET[(value >> (6 * index)) & 63] for index in range(char_count))


def parse_beatrice_state(component: bytes) -> list[tuple[int, int, object]]:
    """Beatrice 状態を (ID, 型, 値) の並びにする。"""
    (declared_size,) = struct.unpack_from("<i", component, 0)
    if declared_size != len(component) - 4:
        raise ValueError(f"状態のサイズが合わない（宣言 {declared_size}、実際 {len(component) - 4}）")
    entries = []
    offset = 4
    while offset < len(component):
        parameter_id, value_type = struct.unpack_from("<hi", component, offset)
        offset += 6
        if value_type == 0:
            (value,) = struct.unpack_from("<i", component, offset)
            offset += 4
        elif value_type == 1:
            (value,) = struct.unpack_from("<d", component, offset)
            offset += 8
        elif value_type == 2:
            (length,) = struct.unpack_from("<i", component, offset)
            offset += 4
            value = component[offset:offset + length].decode("utf-8")
            offset += length
        else:
            raise ValueError(f"未知の型 {value_type}（オフセット {offset - 6}）")
        entries.append((parameter_id, value_type, value))
    return entries


def serialize_beatrice_state(entries: list[tuple[int, int, object]]) -> bytes:
    body = bytearray()
    for parameter_id, value_type, value in entries:
        body += struct.pack("<hi", parameter_id, value_type)
        if value_type == 0:
            body += struct.pack("<i", int(value))
        elif value_type == 1:
            body += struct.pack("<d", float(value))
        elif value_type == 2:
            encoded = str(value).encode("utf-8")
            body += struct.pack("<i", len(encoded)) + encoded
        else:
            raise ValueError(f"未知の型 {value_type}")
    return struct.pack("<i", len(body)) + bytes(body)


def read_model_path_from_raw_state(raw_state: bytes) -> str:
    component = _extract_component(raw_state)[0]
    for parameter_id, value_type, value in parse_beatrice_state(component):
        if parameter_id == MODEL_PARAMETER_ID and value_type == 2:
            return str(value)
    return ""


def build_raw_state_with_model(raw_state: bytes, model_toml: str) -> bytes:
    """pedalboard の raw_state のうち、Beatrice の Model（ID 1）だけを差し替えたものを返す。"""
    component, xml_text, match = _extract_component(raw_state)
    entries = parse_beatrice_state(component)
    replaced = False
    for position, (parameter_id, value_type, _value) in enumerate(entries):
        if parameter_id == MODEL_PARAMETER_ID and value_type == 2:
            entries[position] = (parameter_id, value_type, model_toml)
            replaced = True
    if not replaced:
        raise ValueError("状態に Model（ID 1、文字列）が無い。VST の版が違う可能性がある")
    encoded = juce_base64_encode(serialize_beatrice_state(entries))
    new_xml = xml_text[:match.start(1)] + encoded + xml_text[match.end(1):]
    xml_bytes = new_xml.encode("utf-8")
    return raw_state[:4] + struct.pack("<I", len(xml_bytes)) + xml_bytes + b"\x00"


def _extract_component(raw_state: bytes):
    magic, length = struct.unpack_from("<4sI", raw_state, 0)
    if magic != b"VC2!":
        raise ValueError(f"JUCE の状態ではない（先頭 {magic!r}）")
    xml_text = raw_state[8:8 + length].decode("utf-8")
    match = re.search(r"<IComponent>(.*?)</IComponent>", xml_text, re.S)
    if match is None:
        raise ValueError("状態に <IComponent> が無い")
    return juce_base64_decode(match.group(1)), xml_text, match


# ---------------------------------------------------------------- モデルと VST の場所
def read_model_voices(model_toml: Path) -> list[BeatriceVoice]:
    with open(model_toml, "rb") as handle:
        document = tomllib.load(handle)
    voices = []
    for key, section in document.get("voice", {}).items():
        if not key.isdigit():
            continue
        voices.append(BeatriceVoice(int(key), str(section.get("name", f"ID {key}")), float(section["average_pitch"])))
    voices.sort(key=lambda voice: voice.index)
    if not voices:
        raise BeatriceLoadError(f"モデルに声（[voice.N]）がありません: {model_toml}")
    return voices


def resolve_vst_binary(vst_path: Path) -> Path:
    """バンドル（フォルダ）を渡されたら中の DLL 本体を返す。"""
    if vst_path.is_dir():
        candidates = sorted((vst_path / "Contents" / "x86_64-win").glob("*.vst3"))
        if not candidates:
            raise BeatriceLoadError(f"VST3 バンドルの中に DLL がありません: {vst_path}")
        return candidates[0]
    if not vst_path.is_file():
        raise BeatriceLoadError(f"VST3 が見つかりません: {vst_path}")
    return vst_path


def compute_pitch_shift(target_average_pitch: float, formant_shift: float, average_source_pitch: float,
                        pitch_offset: float = 0.0) -> float:
    """GUI と同じ式で Pitch Shift を求め、VST の刻み（1/8 半音）に丸める。"""
    raw = target_average_pitch + formant_shift - average_source_pitch + pitch_offset
    clamped = float(np.clip(raw, -PITCH_SHIFT_LIMIT, PITCH_SHIFT_LIMIT))
    return round(clamped * 8.0) / 8.0


# ---------------------------------------------------------------- 処理
class BeatriceProcessor:
    """10ms などの固定長ブロックを受け、同じ長さを返す AI 変換。"""

    name = "ai"
    # 実測した遅延（サンプル、48kHz）。voicechanger.latency.estimate_lag_samples で自分の声 30 秒から 4 声とも測り、
    # 1680〜1776 だった（2026-11-17）。ソースの式 round(0.0375 * fs + リサンプラの遅延) とも合う。
    # tests/test_beatrice_host.py が実測との差を確かめる。
    MEASURED_LATENCY_48K = 1728

    def __init__(self, vst_path: Path, model_toml: Path, sample_rate: int, block_size: int,
                 voice_index: int = 0, average_source_pitch: float = 52.0, formant_shift: float = 0.0,
                 pitch_offset: float = 0.0, output_gain_db: float = 0.0, verify: bool = True) -> None:
        import pedalboard  # 読み込みが重いので必要になってから

        self.sample_rate = sample_rate
        self.block_size = block_size
        self.model_toml = Path(model_toml).resolve()
        if not self.model_toml.is_file():
            raise BeatriceLoadError(f"モデルの TOML が見つかりません: {self.model_toml}")
        self.voices = read_model_voices(self.model_toml)
        binary = resolve_vst_binary(Path(vst_path))
        try:
            self._plugin = pedalboard.load_plugin(str(binary))
        except ImportError as error:
            raise BeatriceLoadError(f"VST3 を読み込めません: {binary}: {error}") from error
        self._plugin.raw_state = build_raw_state_with_model(self._plugin.raw_state, str(self.model_toml))
        if read_model_path_from_raw_state(self._plugin.raw_state) != str(self.model_toml):
            raise BeatriceLoadError("状態にモデルのパスが入りませんでした")
        self._plugin.latency_reporting = "Off — Responsive"
        self.voice_index = 0
        self.average_source_pitch = 52.0
        self.formant_shift = 0.0
        self.pitch_offset = 0.0
        self.pitch_shift = 0.0
        self.set_output_gain(output_gain_db)
        self.set_voice(voice_index, average_source_pitch, formant_shift, pitch_offset)
        if verify:
            self.verify_output()

    @property
    def latency_samples(self) -> int:
        return int(round(self.MEASURED_LATENCY_48K * self.sample_rate / 48000))

    def set_voice(self, voice_index: int, average_source_pitch: float | None = None,
                  formant_shift: float | None = None, pitch_offset: float | None = None) -> None:
        valid_indices = [voice.index for voice in self.voices]
        if voice_index not in valid_indices:
            raise ValueError(f"声の番号は {valid_indices} のどれか（受け取ったのは {voice_index}）")
        if average_source_pitch is not None:
            self.average_source_pitch = float(np.clip(average_source_pitch, 0.0, 128.0))
        if formant_shift is not None:
            self.formant_shift = round(float(np.clip(formant_shift, -FORMANT_SHIFT_LIMIT, FORMANT_SHIFT_LIMIT)) * 2) / 2
        if pitch_offset is not None:
            self.pitch_offset = float(np.clip(pitch_offset, -12.0, 12.0))
        target = next(voice for voice in self.voices if voice.index == voice_index)
        self.voice_index = voice_index
        self.pitch_shift = compute_pitch_shift(target.average_pitch, self.formant_shift, self.average_source_pitch,
                                               self.pitch_offset)
        plugin = self._plugin
        plugin.voice = f"ID {voice_index}"
        plugin.lock = "Average Source Pitch"
        plugin.average_source_pitch = self.average_source_pitch
        plugin.formant_shift_st = self.formant_shift
        plugin.pitch_shift_st = self.pitch_shift

    def set_output_gain(self, output_gain_db: float) -> None:
        self.output_gain_db = float(np.clip(output_gain_db, -60.0, 20.0))
        self._plugin.output_gain_db = self.output_gain_db

    def describe(self) -> dict:
        return {
            "model": str(self.model_toml),
            "voices": [{"index": voice.index, "name": voice.name, "average_pitch": voice.average_pitch}
                       for voice in self.voices],
            "voice_index": self.voice_index,
            "average_source_pitch": self.average_source_pitch,
            "formant_shift": self.formant_shift,
            "pitch_offset": self.pitch_offset,
            "pitch_shift": self.pitch_shift,
            "output_gain_db": self.output_gain_db,
        }

    def reset(self) -> None:
        self._plugin.reset()

    def process(self, block: np.ndarray) -> np.ndarray:
        if len(block) != self.block_size:
            raise ValueError(f"ブロック長は {self.block_size}（受け取ったのは {len(block)}）")
        processed = self._plugin.process(np.ascontiguousarray(block, dtype=np.float32).reshape(1, -1),
                                         self.sample_rate, buffer_size=self.block_size, reset=False)
        output = processed[0] if processed.ndim == 2 else processed
        if len(output) != self.block_size:
            fixed = np.zeros(self.block_size, dtype=np.float32)
            fixed[:min(len(output), self.block_size)] = output[:self.block_size]
            return fixed
        return output.astype(np.float32, copy=False)

    def verify_output(self) -> None:
        """合成の母音を 1 秒通し、無音でないことを確かめる（モデルのパスがずれると無音になるため）。"""
        from .tones import harmonic_vowel

        probe = harmonic_vowel(1.0, 150.0, self.sample_rate)
        peak = 0.0
        for start in range(0, len(probe) - self.block_size + 1, self.block_size):
            peak = max(peak, float(np.max(np.abs(self.process(probe[start:start + self.block_size])))))
        self.reset()
        if peak < 1e-3:
            raise BeatriceLoadError(f"モデルを読み込んだのに出力が無音です（最大 {peak:.2e}）。モデルの場所を確認してください")
