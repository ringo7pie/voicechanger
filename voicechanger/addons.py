"""追加の部品（インストーラーに入れられないもの）を、利用者の操作で入れる。

- Beatrice 2 の公式 VST とモデル（AI モード）: 規約で再頒布できない。利用者が公式ページからブラウザで取得した zip を、
  config/tools_manifest.json の SHA-256 で照合して runtime/beatrice/ に展開する（tools/install_engine_deps.ps1 と同じ置き場所）。
- pylibrb（ピッチ・フォルマント）: GPL-2.0 で「or later」の表明が無く、GPLv3 の pedalboard と同じ配布物に入れない。
  PyPI の wheel を URL と SHA-256 を固定して取得し、runtime/lib/ に展開する（voicechanger/__init__.py が sys.path に足す）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from .paths import ADDON_LIB_DIR, APP_DATA, CONFIG_DIR

MANIFEST_PATH = CONFIG_DIR / "tools_manifest.json"
BEATRICE_ITEMS = ("beatrice_vst", "beatrice_model_1")

PYLIBRB_WHEEL = {
    "name": "pylibrb 0.1.2（Rubber Band のバインディング、GPL-2.0）",
    "filename": "pylibrb-0.1.2-cp312-abi3-win_amd64.whl",
    "url": "https://files.pythonhosted.org/packages/f8/e0/5cbac14c76f8e3cf151c2b6444fe340eeaf9f3097016a5ad205bbc4e753f/"
           "pylibrb-0.1.2-cp312-abi3-win_amd64.whl",
    "sha256": "8ab810bd58581da194b4c4bfb8883033dc122a63842ba8d27e2faad084761fe6",
    "page": "https://pypi.org/project/pylibrb/0.1.2/",
    "members": ("pylibrb/", "pylibrb-0.1.2.dist-info/"),
}


class AddonError(RuntimeError):
    """部品を入れられない（利用者に見せる文）。"""


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: zipfile.ZipFile, destination: Path, prefixes: tuple[str, ...] | None = None) -> int:
    """zip の中身を destination の下にだけ展開する（../ や絶対パスは拒否）。展開したファイルの数を返す。"""
    root = destination.resolve()
    count = 0
    for member in archive.infolist():
        name = member.filename.replace("\\", "/")
        if prefixes is not None and not name.startswith(prefixes):
            continue
        relative = PurePosixPath(name)
        if not relative.parts:
            continue
        if relative.is_absolute() or ".." in relative.parts or ":" in relative.parts[0]:
            raise AddonError(f"zip に不正なパスがあります: {member.filename}")
        target = (root / Path(*relative.parts)).resolve()
        if target != root and root not in target.parents:
            raise AddonError(f"zip に不正なパスがあります: {member.filename}")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, open(target, "wb") as sink:
            shutil.copyfileobj(source, sink)
        count += 1
    return count


def _replace_directory(staging: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(destination)


def identify_beatrice_zip(zip_path: Path, manifest: dict | None = None) -> tuple[str, dict]:
    """SHA-256 で、どの Beatrice の zip かを決める。合わなければ AddonError。"""
    manifest = load_manifest() if manifest is None else manifest
    digest = sha256_file(zip_path)
    expected_names = []
    for key in BEATRICE_ITEMS:
        item = manifest["items"][key]
        expected_names.append(item["file_pattern"])
        if item.get("sha256") and digest == item["sha256"].lower():
            return key, item
    raise AddonError(f"{zip_path.name} は対応している Beatrice の zip ではありません（SHA-256 が合いません）。\n"
                     f"対応している zip: {', '.join(expected_names)}")


def install_beatrice_zip(zip_path: Path, app_data: Path = APP_DATA, manifest: dict | None = None) -> str:
    """Beatrice の VST かモデルの zip を照合して展開する。入れた部品の名前を返す。"""
    key, item = identify_beatrice_zip(zip_path, manifest)
    destination = app_data / Path(*PurePosixPath(item["extract_to"]).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if _safe_extract(archive, staging) == 0:
                raise AddonError(f"{zip_path.name} にファイルがありません")
        _replace_directory(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return f"{item['name']}（{item['version']}）"


def download(url: str, target: Path, progress: Callable[[int, int], None] | None = None, timeout: float = 60.0) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VoiceChanger-addon-installer"})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target, "wb") as sink:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        for chunk in iter(lambda: response.read(256 * 1024), b""):
            sink.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)


def install_pylibrb(lib_dir: Path = ADDON_LIB_DIR, wheel_path: Path | None = None,
                    downloader: Callable[[str, Path], None] | None = None) -> str:
    """pylibrb の wheel を取得（wheel_path を渡せばそれを使う）・照合して lib_dir に展開する。"""
    spec = PYLIBRB_WHEEL
    lib_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="pylibrb.", suffix=".tmp", dir=lib_dir.parent))
    try:
        if wheel_path is None:
            wheel_path = work / spec["filename"]
            try:
                (downloader or download)(spec["url"], wheel_path)
            except OSError as error:
                raise AddonError(f"pylibrb を取得できません: {error}") from error
        digest = sha256_file(wheel_path)
        if digest != spec["sha256"]:
            raise AddonError(f"取得した pylibrb の SHA-256 が合いません（{digest}）")
        staging = work / "extracted"
        with zipfile.ZipFile(wheel_path) as archive:
            if _safe_extract(archive, staging, spec["members"]) == 0:
                raise AddonError("wheel に pylibrb がありません")
        for member in spec["members"]:
            name = member.rstrip("/")
            _replace_directory(staging / name, lib_dir / name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return spec["name"]


def pylibrb_installed(lib_dir: Path = ADDON_LIB_DIR) -> bool:
    if (lib_dir / "pylibrb" / "__init__.py").is_file():
        return True
    return importlib.util.find_spec("pylibrb") is not None


def status(settings) -> dict[str, bool]:
    """部品があるか。settings は config.Settings。"""
    return {"beatrice_vst": settings.beatrice_vst.exists(), "beatrice_model": settings.beatrice_model.is_file(),
            "pylibrb": pylibrb_installed()}
