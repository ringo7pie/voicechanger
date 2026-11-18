"""オフライン変換と、聴き比べページの生成。"""
from __future__ import annotations

import html
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import REPO_ROOT, Settings, load_or_measure_self_pitch

LISTEN_DIR = REPO_ROOT / "out" / "listen"


def read_mono(path: Path, sample_rate: int) -> np.ndarray:
    from pedalboard.io import AudioFile

    with AudioFile(str(path)).resampled_to(sample_rate) as audio_file:
        return audio_file.read(audio_file.frames).mean(axis=0).astype(np.float32)


def write_mono(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    from pedalboard.io import AudioFile

    path.parent.mkdir(parents=True, exist_ok=True)
    with AudioFile(str(path), "w", sample_rate, 1) as writer:
        writer.write(audio.reshape(1, -1).astype(np.float32))


def process_audio(processor: Any, audio: np.ndarray, block_size: int) -> tuple[np.ndarray, float]:
    """処理器に通し、遅延ぶんを詰めて入力と頭をそろえる。(出力, 実時間比)。"""
    sample_rate = getattr(processor, "sample_rate", 48000)
    latency = int(getattr(processor, "latency_samples", 0))
    padded_length = int(math.ceil((len(audio) + latency) / block_size)) * block_size
    padded = np.zeros(padded_length, dtype=np.float32)
    padded[:len(audio)] = audio
    output = np.empty(padded_length, dtype=np.float32)
    processor.reset()
    started = time.perf_counter()
    for start in range(0, padded_length, block_size):
        output[start:start + block_size] = processor.process(padded[start:start + block_size])
    seconds = time.perf_counter() - started
    return output[latency:latency + len(audio)], seconds / max(1e-9, len(audio) / sample_rate)


def active_rms_db(audio: np.ndarray, sample_rate: int) -> float:
    """声のある区間（10ms ごとに -50 dBFS 以上）の RMS。"""
    frame = sample_rate // 100
    usable = (len(audio) // frame) * frame
    if usable == 0:
        return -180.0
    frames = audio[:usable].astype(np.float64).reshape(-1, frame)
    energies = np.mean(frames * frames, axis=1)
    active = energies[energies > 10 ** (-50 / 10)]
    if len(active) == 0:
        return -180.0
    return 10 * math.log10(float(np.mean(active)))


def render_file(processor: Any, input_path: Path, output_path: Path, sample_rate: int, block_size: int) -> dict:
    audio = read_mono(input_path, sample_rate)
    output, realtime_factor = process_audio(processor, audio, block_size)
    peak = float(np.max(np.abs(output))) if len(output) else 0.0
    if peak > 0.98:
        output = output * (0.98 / peak)
    write_mono(output_path, output, sample_rate)
    return {"input_seconds": len(audio) / sample_rate, "realtime_factor": realtime_factor,
            "latency_samples": int(getattr(processor, "latency_samples", 0)), "peak": peak}


def make_listen_page(settings: Settings, input_paths: list[Path], output_dir: Path = LISTEN_DIR,
                     ai_pitch_offsets: tuple[float, ...] = (0.0, 2.0), target_rms_db: float = -20.0) -> Path:
    """自分の声を各プリセット・各声で変換し、音量をそろえて聴き比べるページを作る。

    音量は「声のある区間の RMS」を target_rms_db にそろえる。実際の音量との差は表に出す。
    """
    from .beatrice_host import BeatriceProcessor
    from .dsp_shifter import PitchFormantShifter

    sample_rate = int(settings.audio["sample_rate"])
    block_size = int(settings.audio["block_size"])
    output_dir.mkdir(parents=True, exist_ok=True)
    source_pitch = load_or_measure_self_pitch(settings) or 52.0
    ai_state = settings.state.get("ai", {})
    ai_gain = float(ai_state.get("output_gain_db", 0.0))

    variants: list[tuple[str, str, Any]] = []
    for preset in settings.dsp_presets:
        processor = PitchFormantShifter(sample_rate, block_size, preset["semitones"], preset["formant_factor"])
        label = f"ピッチ・フォルマント「{preset['name']}」（{preset['semitones']:+.1f} 半音、×{preset['formant_factor']:.2f}）"
        variants.append((f"dsp_{len(variants)}", label, processor))
    beatrice = BeatriceProcessor(settings.beatrice_vst, settings.beatrice_model, sample_rate, block_size,
                                 average_source_pitch=source_pitch, output_gain_db=ai_gain)
    ai_variants = []
    for voice in beatrice.voices:
        for offset in ai_pitch_offsets:
            ai_variants.append((voice, offset))

    rows = []
    for input_index, input_path in enumerate(input_paths):
        audio = read_mono(input_path, sample_rate)
        original_name = f"in{input_index}_original.wav"
        original_rms = active_rms_db(audio, sample_rate)
        write_mono(output_dir / original_name, _normalize(audio, original_rms, target_rms_db), sample_rate)
        entries = [{"label": "元の声", "file": original_name, "actual_rms_db": original_rms, "rtf": None}]
        for key, label, processor in variants:
            output, realtime_factor = process_audio(processor, audio, block_size)
            entries.append(_save_variant(output_dir, f"in{input_index}_{key}.wav", label, output, sample_rate,
                                         target_rms_db, realtime_factor))
        for voice, offset in ai_variants:
            beatrice.set_voice(voice.index, average_source_pitch=source_pitch, formant_shift=0.0, pitch_offset=offset)
            output, realtime_factor = process_audio(beatrice, audio, block_size)
            label = f"AI「{voice.name}」（微調整 {offset:+.1f} 半音、ピッチシフト {beatrice.pitch_shift:+.3f}）"
            entries.append(_save_variant(output_dir, f"in{input_index}_ai{voice.index}_{offset:+.1f}.wav", label, output,
                                         sample_rate, target_rms_db, realtime_factor))
        rows.append({"input": input_path.name, "entries": entries})

    page = output_dir / "index.html"
    page.write_text(_render_html(rows, source_pitch, target_rms_db), encoding="utf-8")
    (output_dir / "listen.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return page


def _normalize(audio: np.ndarray, rms_db: float, target_rms_db: float) -> np.ndarray:
    if rms_db <= -170:
        return audio
    scaled = audio * (10 ** ((target_rms_db - rms_db) / 20))
    peak = float(np.max(np.abs(scaled))) if len(scaled) else 0.0
    return scaled * (0.98 / peak) if peak > 0.98 else scaled


def _save_variant(output_dir: Path, name: str, label: str, output: np.ndarray, sample_rate: int,
                  target_rms_db: float, realtime_factor: float) -> dict:
    rms = active_rms_db(output, sample_rate)
    write_mono(output_dir / name, _normalize(output, rms, target_rms_db), sample_rate)
    return {"label": label, "file": name, "actual_rms_db": rms, "rtf": realtime_factor}


def _render_html(rows: list[dict], source_pitch: float, target_rms_db: float) -> str:
    sections = []
    for row in rows:
        items = []
        for entry in row["entries"]:
            rtf = "" if entry["rtf"] is None else f"{entry['rtf']:.3f}"
            items.append(
                "<tr><td>{label}</td><td><audio controls preload=\"none\" src=\"{file}\"></audio></td>"
                "<td class=\"num\">{rms:.1f}</td><td class=\"num\">{rtf}</td></tr>".format(
                    label=html.escape(entry["label"]), file=html.escape(entry["file"]), rms=entry["actual_rms_db"], rtf=rtf))
        sections.append(
            f"<section><h2>入力: {html.escape(row['input'])}</h2><table><thead><tr><th>変換</th><th>音</th>"
            f"<th>実際の音量（dBFS）</th><th>実時間比</th></tr></thead><tbody>{''.join(items)}</tbody></table></section>")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceChanger 聴き比べ</title>
<style>
:root {{ --bg:#f6f7f9; --panel:#fff; --text:#1d2330; --muted:#5d6677; --line:#d9dde5; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15181e; --panel:#1e232b; --text:#e6e9ef; --muted:#9aa3b2; --line:#333a46; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,"Segoe UI","Yu Gothic UI",sans-serif; }}
main {{ max-width:1000px; margin:0 auto; padding:16px; }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:12px 0; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }} th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); }} audio {{ width:260px; }}
p {{ color:var(--muted); }}
</style></head><body><main>
<h1>VoiceChanger 聴き比べ</h1>
<p>自分の声の録音を、各プリセットと Beatrice の各声で変換したもの。聴きやすいよう、声のある区間の音量を {target_rms_db:.0f} dBFS にそろえてある（実際の音量は表の値）。
AI の「自分の声の高さ」は録音から測った MIDI {source_pitch:.2f}。「微調整」は操作画面の「ピッチの微調整」と同じ。</p>
<p>声ごとの利用規約に従うこと（README の「権利と注意」）。この録音と変換音はこの PC の中だけで聴く。</p>
{''.join(sections)}
</main></body></html>
"""
