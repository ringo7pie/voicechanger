"""GUI の起動: QApplication、二重起動の防止、ウィンドウ位置の保存、試験用の自動操作。"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import time
from pathlib import Path
from typing import Any, Iterator

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QLockFile, QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from ..__main__ import pid_alive
from ..config import DATA_DIR, SETTINGS_PATH, Settings
from .engine_link import RUNNING, STOPPED, read_control
from .main_window import MainWindow
from .widgets import _draw_icon, make_app_icon

GUI_SETTINGS_PATH = DATA_DIR / "gui.ini"
LOCK_PATH = DATA_DIR / "gui.lock"
SELF_TEST_SETTINGS_PATH = DATA_DIR / "self_test_settings.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m voicechanger.gui", description="VoiceChanger の GUI")
    parser.add_argument("--color-scheme", choices=("system", "light", "dark"), default="system")
    parser.add_argument("--screenshot", type=Path, default=None, help="デバイスを読み込んだら画面を PNG に保存して終わる")
    parser.add_argument("--self-test", action="store_true", help="開始 → 各モード → 停止を自動で行い、結果を JSON に書いて終わる")
    parser.add_argument("--input-wav", type=Path, default=None, help="エンジンの入力をマイクではなく WAV にする（試験用）")
    parser.add_argument("--output-device", default="", help="エンジンの出力をこの起動だけ変える（試験用）")
    parser.add_argument("--result", type=Path, default=DATA_DIR / "gui_self_test.json")
    parser.add_argument("--screenshot-dir", type=Path, default=None, help="--self-test の途中の画面を保存するフォルダ")
    parser.add_argument("--write-icon", type=Path, default=None, help="アイコン（.ico）を書き出して終わる")
    return parser


def engine_arguments_from(arguments: argparse.Namespace) -> list[str]:
    extra: list[str] = []
    if arguments.input_wav is not None:
        extra += ["--input-wav", str(arguments.input_wav), "--loop-input"]
    if arguments.output_device:
        extra += ["--output-device", arguments.output_device]
    if arguments.self_test:
        extra += ["--no-monitor", "--no-obs", "--settings-file", str(SELF_TEST_SETTINGS_PATH)]
    return extra


def prepare_self_test_settings() -> None:
    """自己試験は本物の data/settings.json を書き換えないよう、複製で動かす。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS_PATH.is_file():
        shutil.copyfile(SETTINGS_PATH, SELF_TEST_SETTINGS_PATH)
    elif SELF_TEST_SETTINGS_PATH.exists():
        SELF_TEST_SETTINGS_PATH.unlink()


def apply_color_scheme(choice: str) -> None:
    if choice == "system":
        return
    hints = QGuiApplication.styleHints()
    scheme = Qt.ColorScheme.Dark if choice == "dark" else Qt.ColorScheme.Light
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(scheme)


def write_ico(path: Path, sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> None:
    """複数の大きさの PNG を入れた .ico を書く（Windows Vista 以降は PNG 入りの ICO を読める）。"""
    images = []
    for size in sizes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        _draw_icon(size).save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))
        buffer.close()
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    data = b""
    for size, png in images:
        edge = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + data)


class SelfTest:
    """GUI を実際に操作して確かめる。WAV を入力、耳に聞こえないデバイスを出力にして使う。"""

    def __init__(self, window: MainWindow, result_path: Path, screenshot_dir: Path | None) -> None:
        self.window = window
        self.result_path = result_path
        self.screenshot_dir = screenshot_dir
        self.result: dict[str, Any] = {"ok": False, "steps": []}
        self.statuses: list[dict] = []
        self.exit_code = 1
        window.bridge.statusReceived.connect(self._record)
        self._steps = self._run()
        self._waiting: tuple[Any, float] | None = None
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def _record(self, status: dict) -> None:
        self.statuses.append(status)

    def _step(self, name: str, ok: bool, **details: Any) -> None:
        self.result["steps"].append({"name": name, "ok": ok, **details})

    def _shot(self, name: str) -> None:
        if self.screenshot_dir is not None:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            self.window.grab().save(str(self.screenshot_dir / f"{name}.png"))

    def _tick(self) -> None:
        if self._waiting is not None:
            condition, deadline = self._waiting
            if not condition() and time.monotonic() < deadline:
                return
            self._waiting = None
        try:
            request = next(self._steps)
        except StopIteration:
            self._finish()
            return
        except Exception as error:  # noqa: BLE001
            self._step("例外", False, error=f"{type(error).__name__}: {error}")
            self._finish()
            return
        kind, value, timeout = request
        if kind == "sleep":
            until = time.monotonic() + value
            self._waiting = (lambda: time.monotonic() >= until, until + 1)
        else:
            self._waiting = (value, time.monotonic() + timeout)

    def _finish(self) -> None:
        self._timer.stop()
        self.result["ok"] = bool(self.result["steps"]) and all(step["ok"] for step in self.result["steps"])
        self.exit_code = 0 if self.result["ok"] else 1
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.window.close()
        QApplication.instance().exit(self.exit_code)

    def _peak_since(self, index: int) -> float:
        peaks = [status.get("levels", {}).get("output_peak_db", -180.0) for status in self.statuses[index:]]
        return max(peaks) if peaks else -180.0

    def _run(self) -> Iterator[tuple]:
        window = self.window
        if read_control() is not None:
            self._step("エンジンが既に起動していない", False)
            return
        yield ("wait", lambda: window._devices_ready, 30.0)
        self._step("デバイスの一覧を読めた", window._devices_ready,
                   input=window.input_combo.currentText(), output=window.output_combo.currentText())
        started = time.monotonic()
        window.start_button.click()
        yield ("wait", lambda: window.link.state == RUNNING and window._status is not None, 60.0)
        self._step("開始で動作中になった", window.link.state == RUNNING, seconds=round(time.monotonic() - started, 1))
        if window.link.state != RUNNING:
            return
        engine_pid = window.link._client.control.pid
        self._shot("running")
        for mode in ("dsp", "ai", "mute"):
            window.mode_buttons[mode].click()
            yield ("wait", lambda selected=mode: (window._status or {}).get("mode") == selected, 10.0)
            first = len(self.statuses)
            yield ("sleep", 3.0, 0)
            peak = self._peak_since(first)
            expect_sound = mode != "mute"
            self._step(f"{mode} に切り替わり、出力が{'鳴る' if expect_sound else '無音'}",
                       (window._status or {}).get("mode") == mode and ((peak > -40.0) if expect_sound else (peak < -90.0)),
                       output_peak_db=peak)
            self._shot(f"mode_{mode}")
        window.mode_buttons["dsp"].click()
        yield ("wait", lambda: (window._status or {}).get("mode") == "dsp", 10.0)
        window.semitones_slider.slider.setValue(window.semitones_slider.slider.value() + 4)  # +2 半音
        expected = window.semitones_slider.value()
        yield ("wait", lambda: ((window._status or {}).get("processors", {}).get("dsp") or {}).get("semitones") == expected, 5.0)
        actual = ((window._status or {}).get("processors", {}).get("dsp") or {}).get("semitones")
        self._step("スライダの値がエンジンに届く", actual == expected, expected=expected, actual=actual)
        stats = (window._status or {}).get("buffers", {})
        self._step("変換エラーが無い", stats.get("processor_errors", 0) == 0, buffers=stats,
                   processing=(window._status or {}).get("processing"))
        window.start_button.click()
        yield ("wait", lambda: window.link.state == STOPPED, 20.0)
        yield ("sleep", 0.5, 0)
        self._step("停止でエンジンのプロセスが消えた", window.link.state == STOPPED and not pid_alive(engine_pid),
                   engine_pid=engine_pid)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    QApplication.setApplicationName("VoiceChanger")
    QApplication.setOrganizationName("VoiceChanger")
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(make_app_icon())
    apply_color_scheme(arguments.color_scheme)

    if arguments.write_icon is not None:
        write_ico(arguments.write_icon)
        return 0

    testing = arguments.self_test or arguments.screenshot is not None
    lock = None
    if not testing:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(LOCK_PATH))
        lock.setStaleLockTime(0)  # 時間では古いと見なさない（落ちたプロセスの鍵は Qt が PID で見分ける）
        if not lock.tryLock(200):
            QMessageBox.information(None, "VoiceChanger", "VoiceChanger は既に開いています。タスクバーの窓を使ってください。")
            return 0

    if arguments.self_test:
        prepare_self_test_settings()
        window = MainWindow(engine_arguments=engine_arguments_from(arguments),
                            settings_factory=lambda: Settings(settings_path=SELF_TEST_SETTINGS_PATH))
    else:
        window = MainWindow(engine_arguments=engine_arguments_from(arguments))
    gui_settings = QSettings(str(GUI_SETTINGS_PATH), QSettings.Format.IniFormat)
    geometry = gui_settings.value("geometry")
    if isinstance(geometry, QByteArray) and not testing:
        window.restoreGeometry(geometry)
    window.show()

    if not testing:
        app.aboutToQuit.connect(lambda: gui_settings.setValue("geometry", window.saveGeometry()))
    if arguments.screenshot is not None:
        def save_and_quit() -> None:
            arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(arguments.screenshot))
            window.close()
            app.exit(0)

        def wait_devices(deadline: float = time.monotonic() + 20.0) -> None:
            if window._devices_ready or time.monotonic() > deadline:
                QTimer.singleShot(300, save_and_quit)
            else:
                QTimer.singleShot(100, wait_devices)

        QTimer.singleShot(100, wait_devices)
    self_test = None
    if arguments.self_test:
        self_test = SelfTest(window, arguments.result, arguments.screenshot_dir)
        QTimer.singleShot(200, self_test.start)
    exit_code = app.exec()
    if lock is not None:
        lock.unlock()
    return exit_code
