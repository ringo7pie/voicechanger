# PyInstaller の設定（onedir・窓アプリ）。tools\build_installer.ps1 から呼ぶ。
# 1 つの VoiceChanger.exe が GUI とエンジンを兼ねる（voicechanger/app_entry.py。エンジンは「VoiceChanger.exe engine ...」）。
# pylibrb は入れない（GPL-2.0。GUI から利用者が取得する: voicechanger/addons.py）。
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

repo = Path(SPECPATH).parent

datas = [
    (str(repo / "voicechanger" / "web" / "index.html"), "voicechanger/web"),
    (str(repo / "config" / "voicechanger.json"), "config"),
    (str(repo / "config" / "tools_manifest.json"), "config"),
    (str(repo / "LICENSE"), "."),
    (str(repo / "packaging" / "THIRD-PARTY-NOTICES.txt"), "."),
]

hiddenimports = collect_submodules("voicechanger") + ["sounddevice", "pedalboard", "obsws_python"]

analysis = Analysis(
    [str(repo / "voicechanger" / "app_entry.py")],
    pathex=[str(repo)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pylibrb", "tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VoiceChanger",
    icon=str(repo / "voicechanger" / "gui" / "voicechanger.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="VoiceChanger", upx=False)
