"""python -m voicechanger.gui（pythonw でも動く。例外は data/logs/gui.log に書き、ダイアログで知らせる）。"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback

from ..config import DATA_DIR

LOG_PATH = DATA_DIR / "logs" / "gui.log"


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw では None
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def show_fatal(message: str) -> None:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            QApplication([])
        QMessageBox.critical(None, "VoiceChanger", f"{message}\n\n詳しくは {LOG_PATH} を見てください。")
    except Exception:  # noqa: BLE001 - Qt が使えないときは諦める
        pass


def main() -> int:
    configure_logging()
    log = logging.getLogger("voicechanger.gui")

    def excepthook(exception_type, exception, trace) -> None:  # Qt のスロットで起きた例外もここに来る
        log.error("捕まえていない例外:\n%s", "".join(traceback.format_exception(exception_type, exception, trace)))

    sys.excepthook = excepthook
    try:
        from .app import main as app_main
    except ImportError as error:
        log.exception("GUI を読み込めません")
        show_fatal(f"GUI を読み込めません: {error}\nPySide6 が入っていない可能性があります（tools\\install_engine_deps.ps1）。")
        return 1
    try:
        return app_main(sys.argv[1:])
    except Exception as error:  # noqa: BLE001
        log.exception("GUI が止まりました")
        show_fatal(f"GUI が止まりました: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
