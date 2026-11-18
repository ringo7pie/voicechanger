"""画面に依らない判断（Qt なしで試験できるようにここへ集める）。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .engine_link import ERROR, RUNNING, STARTING, STOPPED, STOPPING

MODE_LABELS = {
    "dsp": "ピッチ・フォルマント",
    "ai": "AI（Beatrice）",
    "mute": "無音",
    "through": "素の声（確認用）",
}

STATE_LABELS = {
    STOPPED: ("停止中", "idle"),
    STARTING: ("起動中…", "busy"),
    RUNNING: ("動作中", "ok"),
    STOPPING: ("停止しています…", "busy"),
    ERROR: ("エラー", "error"),
}


def _first_present(candidates: list[str | None], available: list[str]) -> str | None:
    lowered = {name.casefold(): name for name in available}
    for candidate in candidates:
        if candidate and candidate.casefold() in lowered:
            return lowered[candidate.casefold()]
    return None


def choose_input(saved: str | None, inputs: list[str], configured: str | None) -> str | None:
    """入力の初期値: 保存した入力 → 設定の入力 → 一覧の先頭。"""
    return _first_present([saved, configured], inputs) or (inputs[0] if inputs else None)


def choose_output(saved: str | None, outputs: list[str], monitor_default: str | None) -> str | None:
    """出力の初期値: 保存した出力 → CABLE Input → 設定のモニター用デバイス（ヘッドホン）→ 一覧の先頭。"""
    cable = next((name for name in outputs if name.casefold().startswith("cable input")), None)
    return _first_present([saved, cable, monitor_default], outputs) or (outputs[0] if outputs else None)


def choose_monitor(saved: str | None, outputs: list[str], configured: str | None) -> str | None:
    """モニターの初期値: 保存したもの → 設定のもの → 無し。"""
    return _first_present([saved, configured], outputs)


def is_virtual_cable(name: str | None) -> bool:
    return bool(name) and name.casefold().startswith("cable input")


def default_render_warning(default_output: str | None) -> str | None:
    """Windows の既定の再生デバイスが仮想ケーブルなら警告する（VB-CABLE の導入や再起動で切り替わることがある）。"""
    if not is_virtual_cable(default_output):
        return None
    return (f"Windows の既定の再生デバイスが「{default_output}」になっています。ゲームなどの音が仮想マイクに流れ込み、"
            "録画の声に混ざります。サウンドの設定（mmsys.cpl）の「再生」タブで、普段聴いている機器を"
            "「既定のデバイス」「既定の通信デバイス」に戻し、「再読込」を押してください")


def input_loop_warning(input_device: str | None) -> str | None:
    """入力が仮想マイク（CABLE Output）なら警告する。出力を CABLE Input にすると、変換後の声が入力へ戻って回り続ける。"""
    if not input_device or not input_device.casefold().startswith("cable output"):
        return None
    return (f"入力が仮想マイク「{input_device}」になっています。出力を CABLE Input にすると音が回り続けます。"
            "入力は物理マイク（例: マイク (High Definition Audio Device)）にしてください")


def output_route_hint(output_device: str | None) -> str | None:
    """出力が仮想マイクでないときの案内。他のアプリが「マイク」を録ると地声になることを伝える。"""
    if not output_device or is_virtual_cable(output_device):
        return None
    return (f"変換後の声は「{output_device}」だけに出ています。他のアプリ（Windows の録画・Discord など）が"
            "「マイク」を録ると地声のままです。変換後の声を録るには、出力を CABLE Input にし、"
            "録るアプリのマイクを CABLE Output にします（VB-CABLE が必要）")


def state_label(state: str) -> tuple[str, str]:
    """(表示の文字, 色の種類 idle/busy/ok/error)。"""
    return STATE_LABELS.get(state, (state, "idle"))


def meter_fraction(decibels: float | None, floor_db: float = -60.0) -> float:
    if decibels is None or not math.isfinite(decibels):
        return 0.0
    return max(0.0, min(1.0, (decibels - floor_db) / -floor_db))


def meter_color(decibels: float | None) -> str:
    if decibels is None:
        return "normal"
    if decibels > -1.5:
        return "clip"
    if decibels > -9.0:
        return "hot"
    return "normal"


def format_db(decibels: float | None) -> str:
    if decibels is None or decibels <= -90:
        return "-∞ dB"
    return f"{decibels:.0f} dB"


def format_signed(value: float, digits: int = 1, unit: str = "") -> str:
    text = f"{value:+.{digits}f}" if abs(value) >= 10 ** -(digits + 1) else f"{0:.{digits}f}"
    return text + unit


@dataclass(frozen=True)
class Warning:
    level: str  # danger / info
    text: str


def warnings_from_status(status: dict[str, Any] | None, output_device: str | None) -> list[Warning]:
    """状態から、画面の上に出す警告を作る。"""
    if not status:
        return []
    items: list[Warning] = []
    if status.get("mode") == "through" or status.get("pending_mode") == "through":
        remaining = status.get("through_remaining_s")
        suffix = f"（あと {math.ceil(remaining)} 秒で無音に戻ります）" if remaining is not None else ""
        items.append(Warning("danger", "素の声が出力に出ています" + suffix))
    if status.get("monitor"):
        items.append(Warning("danger", "モニターが有効です。ヘッドホンの音は OBS では「ゲーム音」として録画に入ります"))
    hint = output_route_hint(output_device)
    if hint:
        items.append(Warning("info", hint))
    notice = status.get("notice")
    if notice:
        items.append(Warning("info", str(notice)))
    for mode, reason in (status.get("unavailable") or {}).items():
        items.append(Warning("info", f"{MODE_LABELS.get(mode, mode)} は使えません: {reason}"))
    return items


def stats_rows(status: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not status:
        return []
    buffers = status.get("buffers") or {}
    processing = status.get("processing") or {}
    obs = status.get("obs")
    obs_text = "-" if not obs else ("録画中" if obs.get("recording") else ("接続" if obs.get("connected") else "未接続"))
    return [
        ("遅延（そろえた値）", f"{status.get('aligned_latency_ms', 0):.0f} ms"),
        ("処理 平均 / 最悪", f"{processing.get('average_block_ms', 0):.2f} / {processing.get('worst_block_ms', 0):.2f} ms"),
        ("出力の途切れ", str(buffers.get("output_underruns", 0))),
        ("ずれ補正 捨て/足し/強制", f"{buffers.get('drift_drops', 0)} / {buffers.get('drift_inserts', 0)} / {buffers.get('forced_drops', 0)}"),
        ("変換エラー", str(buffers.get("processor_errors", 0))),
        ("OBS", obs_text),
    ]


def monitor_available(status: dict[str, Any] | None) -> bool:
    """エンジンがモニターのストリームを開いているか（出力と同じデバイスなら開かない）。"""
    if not status:
        return False
    devices = (status.get("backend") or {}).get("devices") or {}
    return bool(devices.get("monitor"))
