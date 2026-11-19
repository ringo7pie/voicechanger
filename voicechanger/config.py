"""設定の読み書き。

- 既定値: config/voicechanger.json（git 管理）
- 利用者が変えた値: data/settings.json（git 管理外）。mode・dsp・ai・output_gain_db だけを保存する。
  確認用の素の声（through）とモニターは保存しない（docs: knowledge/10-silver/areas/audio-routing.md）。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .paths import APP_DATA, CONFIG_DIR, DATA_DIR, REPO_ROOT

DEFAULT_CONFIG_PATH = CONFIG_DIR / "voicechanger.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
SELF_PITCH_PATH = DATA_DIR / "self_pitch.json"

MODES = ("dsp", "ai", "mute", "through")
PERSISTABLE_MODES = ("dsp", "ai", "mute")
PERSISTED_KEYS = ("mode", "output_gain_db", "dsp", "ai", "devices")
DEVICE_KEYS = ("input_device", "output_device", "monitor_device")


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_repo_path(value: str) -> Path:
    """設定の相対パス（runtime/... など）を、書き込む場所の根（ソースはリポジトリ）から解決する。"""
    path = Path(value)
    return path if path.is_absolute() else (APP_DATA / path)


class Settings:
    """既定値と保存値を合わせた設定。"""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH, settings_path: Path | None = SETTINGS_PATH) -> None:
        self.config_path = config_path
        self.settings_path = settings_path
        self.config = load_json(config_path)
        saved: dict[str, Any] = {}
        if settings_path is not None and settings_path.is_file():
            try:
                saved = load_json(settings_path)
            except (OSError, json.JSONDecodeError):
                saved = {}
        self.state = merge(self.config["defaults"], {key: saved[key] for key in PERSISTED_KEYS if key in saved})
        if self.state.get("mode") not in PERSISTABLE_MODES:
            self.state["mode"] = "mute"
        devices = self.state.get("devices")
        if not isinstance(devices, dict):
            devices = {}
        self.state["devices"] = {key: str(devices[key]) for key in DEVICE_KEYS if devices.get(key)}
        # 音声の設定は、既定の設定に保存したデバイス名を重ねたもの（この起動だけの上書きは audio を直接書き換える）
        self._audio = merge(self.config["audio"], self.state["devices"])

    # 読み出し
    @property
    def audio(self) -> dict[str, Any]:
        return self._audio

    @property
    def saved_devices(self) -> dict[str, str]:
        """利用者が選んで保存したデバイス名（選んでいないものは含まない）。"""
        return dict(self.state.get("devices", {}))

    @property
    def safety(self) -> dict[str, Any]:
        return self.config["safety"]

    @property
    def control(self) -> dict[str, Any]:
        return self.config["control"]

    @property
    def dsp_presets(self) -> list[dict[str, Any]]:
        return list(self.config.get("dsp_presets", []))

    @property
    def beatrice_vst(self) -> Path:
        return resolve_repo_path(self.config["beatrice"]["vst"])

    @property
    def beatrice_model(self) -> Path:
        return resolve_repo_path(self.config["beatrice"]["model"])

    @property
    def self_voice_dir(self) -> Path | None:
        """自分の声の録音のフォルダ。設定の self_voice_dir、無ければ環境変数 VOICECHANGER_SELF_VOICE_DIR。"""
        value = self.config.get("self_voice_dir") or os.environ.get("VOICECHANGER_SELF_VOICE_DIR")
        return Path(value) if value else None

    @property
    def startup_mode(self) -> str:
        mode = self.state.get("mode")
        return mode if mode in PERSISTABLE_MODES else "mute"

    # 書き込み
    def update_state(self, changes: dict[str, Any]) -> None:
        for key in changes:
            if key not in PERSISTED_KEYS:
                raise KeyError(f"保存しない項目: {key}")
        if "mode" in changes and changes["mode"] not in PERSISTABLE_MODES:
            changes = {key: value for key, value in changes.items() if key != "mode"}
        self.state = merge(self.state, changes)

    def set_devices(self, **names: str | None) -> None:
        """入力・出力・モニターのデバイス名を変える（input_device= など）。None や空文字は「選んでいない」に戻す。"""
        devices = dict(self.state.get("devices", {}))
        for key, value in names.items():
            if key not in DEVICE_KEYS:
                raise KeyError(f"デバイスの項目ではない: {key}")
            if value:
                devices[key] = str(value)
            else:
                devices.pop(key, None)
        self.state["devices"] = devices
        self._audio = merge(self.config["audio"], devices)

    def save(self) -> None:
        if self.settings_path is None:
            return
        document = {key: self.state[key] for key in PERSISTED_KEYS if key in self.state}
        if document.get("mode") not in PERSISTABLE_MODES:
            document["mode"] = "mute"
        save_json_atomic(self.settings_path, document)


def load_or_measure_self_pitch(settings: Settings, cache_path: Path = SELF_PITCH_PATH) -> float | None:
    """自分の声の高さ（MIDI）を返す。初回は録音から測って data/ に保存する。録音が無ければ None。"""
    if cache_path.is_file():
        try:
            return float(load_json(cache_path)["midi"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass
    voice_dir = settings.self_voice_dir
    if voice_dir is None or not voice_dir.is_dir():
        return None
    wav_paths = sorted(voice_dir.glob("*.wav"))
    if not wav_paths:
        return None
    from .pitch_estimate import estimate_average_source_pitch

    result = estimate_average_source_pitch(wav_paths)
    result["source"] = str(voice_dir)
    save_json_atomic(cache_path, result)
    return float(result["midi"])
