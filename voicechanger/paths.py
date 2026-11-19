"""ファイルの置き場所。

ソースから動かすとき（`.venv` の python）と、インストーラーで入れた実行ファイル（PyInstaller で凍結）のときで切り替える。

- 読むだけの資源（`config/`、`voicechanger/web/`）: ソースはリポジトリ、凍結時は展開先（`sys._MEIPASS`）。
- 書き込む場所 APP_DATA: ソースはリポジトリのルート（今までどおり `data/`・`runtime/`）、
  凍結時は `%LOCALAPPDATA%\\VoiceChanger`（インストール先の Program Files は書けないため）。
  環境変数 VOICECHANGER_DATA_DIR があれば、どちらのときもそれを使う（試験用）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_DIR_ENV = "VOICECHANGER_DATA_DIR"
FROZEN = bool(getattr(sys, "frozen", False))
REPO_ROOT = Path(__file__).resolve().parent.parent


def resource_root(frozen: bool = FROZEN) -> Path:
    """config/ を含む、読むだけの資源の根。"""
    if frozen:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return REPO_ROOT


def app_data_root(frozen: bool = FROZEN, environ: dict[str, str] | None = None) -> Path:
    """書き込む場所の根（この下に data/ と runtime/ を置く）。"""
    environ = os.environ if environ is None else environ
    override = environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    if frozen:
        local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "VoiceChanger"
    return REPO_ROOT


RESOURCE_ROOT = resource_root()
APP_DATA = app_data_root()
CONFIG_DIR = RESOURCE_ROOT / "config"
DATA_DIR = APP_DATA / "data"
RUNTIME_DIR = APP_DATA / "runtime"
ADDON_LIB_DIR = RUNTIME_DIR / "lib"


def add_addon_lib_to_path() -> None:
    """GUI から取得した追加部品（pylibrb）を import できるようにする。"""
    if ADDON_LIB_DIR.is_dir() and str(ADDON_LIB_DIR) not in sys.path:
        sys.path.insert(0, str(ADDON_LIB_DIR))
