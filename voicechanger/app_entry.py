"""インストーラー版の実行ファイル（PyInstaller で凍結）の入口。

  VoiceChanger.exe [GUI の引数]      GUI（python -m voicechanger.gui と同じ）
  VoiceChanger.exe engine <コマンド>  エンジンの CLI（python -m voicechanger と同じ。GUI が裏で呼ぶ）
"""
from __future__ import annotations

import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "engine":
        from voicechanger.__main__ import main as engine_main

        return engine_main(arguments[1:])
    from voicechanger.gui.__main__ import main as gui_main

    sys.argv = [sys.argv[0], *arguments]
    return gui_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
