"""メインウィンドウ。エンジン（別プロセス）を起動・停止し、操作 API で状態を読み、命令を送る。"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import addons
from ..config import SELF_PITCH_PATH, Settings
from . import view_state
from .engine_link import ENGINE_LOG, ERROR, RUNNING, STARTING, STOPPED, STOPPING, EngineLink, list_devices_subprocess
from .widgets import BannerArea, LevelMeter, StatusDot, ValueSlider, make_app_icon

log = logging.getLogger("voicechanger.gui")

MODES = ("dsp", "ai", "mute", "through")
NO_MONITOR = ""


class LinkBridge(QObject):
    """裏スレッドからのコールバックを、GUI のスレッドへ渡すシグナル。"""

    stateChanged = Signal(str, str)
    statusReceived = Signal(object)
    commandFinished = Signal(str, object, object)
    devicesLoaded = Signal(object)
    devicesFailed = Signal(str)
    addonFinished = Signal(bool, str)


class MainWindow(QMainWindow):
    def __init__(self, *, link_factory: Callable[..., Any] | None = None,
                 device_lister: Callable[[], dict] = list_devices_subprocess,
                 settings_factory: Callable[[], Settings] = Settings,
                 engine_arguments: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("VoiceChanger")
        self.setWindowIcon(make_app_icon())
        self.bridge = LinkBridge(self)
        self._settings_factory = settings_factory
        self._device_lister = device_lister
        self.engine_arguments = list(engine_arguments or [])
        self._status: dict | None = None
        self._state = STOPPED
        self._state_message = ""
        self._restart_after_stop = False
        self._populating = False
        self._devices_ready = False
        self._last_panel = "dsp"
        self._error_text = ""
        self._notice_text = ""
        self._default_output: str | None = None
        self._voices_signature: tuple = ()
        factory = link_factory or EngineLink
        self.link = factory(self.bridge.stateChanged.emit, self.bridge.statusReceived.emit,
                            self.bridge.commandFinished.emit)
        self.bridge.stateChanged.connect(self._on_state)
        self.bridge.statusReceived.connect(self._on_status)
        self.bridge.commandFinished.connect(self._on_command_finished)
        self.bridge.devicesLoaded.connect(self._on_devices_loaded)
        self.bridge.devicesFailed.connect(self._on_devices_failed)
        self.bridge.addonFinished.connect(self._on_addon_finished)
        self._addon_busy = False
        self._build()
        self._load_saved_values()
        self._refresh_view()
        self.refresh_devices()
        QTimer.singleShot(0, self._attach_existing)

    # ================================================================ 画面の組み立て
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        device_box = QGroupBox("デバイス")
        device_grid = QGridLayout(device_box)
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self.monitor_combo = QComboBox()
        for combo in (self.input_combo, self.output_combo, self.monitor_combo):
            combo.setMinimumContentsLength(28)
            combo.activated.connect(self._on_device_changed)
        self.refresh_button = QPushButton("再読込")
        self.refresh_button.setToolTip("デバイスの一覧を取り直す")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.monitor_check = QCheckBox("モニターでも聴く（出力と別のデバイスのとき。保存しない）")
        self.monitor_check.clicked.connect(self._on_monitor_clicked)
        device_grid.addWidget(QLabel("入力"), 0, 0)
        device_grid.addWidget(self.input_combo, 0, 1)
        device_grid.addWidget(self.refresh_button, 0, 2)
        device_grid.addWidget(QLabel("出力"), 1, 0)
        device_grid.addWidget(self.output_combo, 1, 1, 1, 2)
        device_grid.addWidget(QLabel("モニター"), 2, 0)
        device_grid.addWidget(self.monitor_combo, 2, 1, 1, 2)
        device_grid.addWidget(self.monitor_check, 3, 1, 1, 2)
        device_grid.setColumnStretch(1, 1)
        root.addWidget(device_box)

        control_row = QHBoxLayout()
        self.start_button = QPushButton("▶ 開始")
        self.start_button.setMinimumHeight(40)
        self.start_button.setMinimumWidth(140)
        bold = QFont(self.start_button.font())
        bold.setBold(True)
        bold.setPointSizeF(bold.pointSizeF() * 1.1)
        self.start_button.setFont(bold)
        self.start_button.clicked.connect(self._on_start_clicked)
        self.state_dot = StatusDot()
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.latency_label = QLabel()
        self.log_button = QPushButton("ログを開く")
        self.log_button.clicked.connect(self._open_log)
        control_row.addWidget(self.start_button)
        control_row.addSpacing(8)
        control_row.addWidget(self.state_dot)
        control_row.addWidget(self.state_label, 1)
        control_row.addWidget(self.latency_label)
        control_row.addWidget(self.log_button)
        root.addLayout(control_row)

        self.banners = BannerArea()
        root.addWidget(self.banners)

        root.addWidget(self._build_addon_box())

        mode_box = QGroupBox("モード")
        mode_layout = QHBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode in MODES:
            button = QPushButton(view_state.MODE_LABELS[mode])
            button.setCheckable(True)
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _checked=False, selected=mode: self._on_mode_clicked(selected))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_layout.addWidget(button)
        root.addWidget(mode_box)

        self.panels = QStackedWidget()
        self.panels.addWidget(self._build_dsp_page())
        self.panels.addWidget(self._build_ai_page())
        root.addWidget(self.panels)

        output_box = QGroupBox("出力")
        output_layout = QVBoxLayout(output_box)
        self.gain_slider = ValueSlider("全体の音量", -20.0, 20.0, 0.5, lambda value: view_state.format_signed(value, 1, " dB"))
        self.gain_slider.valueEdited.connect(lambda value: self._post("/api/output_gain", {"output_gain_db": value}))
        output_layout.addWidget(self.gain_slider)
        meter_grid = QGridLayout()
        self.input_meter = LevelMeter()
        self.output_meter = LevelMeter()
        self.input_db_label = QLabel("-∞ dB")
        self.output_db_label = QLabel("-∞ dB")
        for row, (name, meter, label) in enumerate((("入力", self.input_meter, self.input_db_label),
                                                     ("出力", self.output_meter, self.output_db_label))):
            label.setMinimumWidth(60)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            name_label = QLabel(name)
            name_label.setMinimumWidth(110)
            meter_grid.addWidget(name_label, row, 0)
            meter_grid.addWidget(meter, row, 1)
            meter_grid.addWidget(label, row, 2)
        meter_grid.setColumnStretch(1, 1)
        output_layout.addLayout(meter_grid)
        root.addWidget(output_box)

        stats_box = QGroupBox("状態")
        self.stats_grid = QGridLayout(stats_box)
        self.stats_labels: dict[str, QLabel] = {}
        names = [name for name, _value in view_state.stats_rows({"buffers": {}, "processing": {}})]
        for index, name in enumerate(names):
            value_label = QLabel("-")
            self.stats_labels[name] = value_label
            row, column = divmod(index, 2)
            self.stats_grid.addWidget(QLabel(name), row, column * 2)
            self.stats_grid.addWidget(value_label, row, column * 2 + 1)
        self.stats_grid.setColumnStretch(1, 1)
        self.stats_grid.setColumnStretch(3, 1)
        root.addWidget(stats_box)
        root.addStretch(1)
        self.setCentralWidget(central)
        self.resize(640, 820)

    def _build_addon_box(self) -> QWidget:
        """インストーラーに入れられない部品（Beatrice・pylibrb）を入れる欄。全部そろったら隠す。"""
        self.addon_box = QGroupBox("追加の部品")
        layout = QGridLayout(self.addon_box)
        self.beatrice_status_label = QLabel()
        self.beatrice_status_label.setWordWrap(True)
        self.beatrice_status_label.setOpenExternalLinks(True)
        self.beatrice_button = QPushButton("Beatrice の zip を選ぶ…")
        self.beatrice_button.setToolTip("公式ページから取得した VST とモデルの zip（2 つ。まとめて選べる）")
        self.beatrice_button.clicked.connect(self._on_beatrice_clicked)
        self.pylibrb_status_label = QLabel()
        self.pylibrb_status_label.setWordWrap(True)
        self.pylibrb_status_label.setOpenExternalLinks(True)
        self.pylibrb_button = QPushButton("部品を取得")
        self.pylibrb_button.setToolTip(f"PyPI から {addons.PYLIBRB_WHEEL['filename']} を取得し、SHA-256 を照合して入れる")
        self.pylibrb_button.clicked.connect(self._on_pylibrb_clicked)
        layout.addWidget(self.beatrice_status_label, 0, 0)
        layout.addWidget(self.beatrice_button, 0, 1)
        layout.addWidget(self.pylibrb_status_label, 1, 0)
        layout.addWidget(self.pylibrb_button, 1, 1)
        layout.setColumnStretch(0, 1)
        self._refresh_addons()
        return self.addon_box

    def _refresh_addons(self) -> None:
        try:
            present = addons.status(self._settings_factory())
        except Exception as error:  # noqa: BLE001 - 表示できないだけで止めない
            log.warning("部品の状態を読めません: %s", error)
            self.addon_box.setVisible(False)
            return
        manifest_pages = ("https://prj-beatrice.com/", "https://prj-beatrice.com/2.0.0-rc.0-official-model-1-terms.html")
        beatrice_ok = present["beatrice_vst"] and present["beatrice_model"]
        missing = [name for name, ok in (("VST", present["beatrice_vst"]), ("モデル", present["beatrice_model"])) if not ok]
        self.beatrice_status_label.setText(
            "AI（Beatrice 2）: 入っています" if beatrice_ok else
            f"AI（Beatrice 2）: {'・'.join(missing)} がありません。<a href=\"{manifest_pages[0]}\">VST</a> と "
            f"<a href=\"{manifest_pages[1]}\">公式モデル 1</a>（規約に同意して取得）の zip を選んでください")
        self.pylibrb_status_label.setText(
            "ピッチ・フォルマント（pylibrb）: 入っています" if present["pylibrb"] else
            f"ピッチ・フォルマント: 部品（<a href=\"{addons.PYLIBRB_WHEEL['page']}\">pylibrb</a>、GPL-2.0）がありません。"
            "インターネットから取得します")
        self.beatrice_button.setVisible(not beatrice_ok)
        self.pylibrb_button.setVisible(not present["pylibrb"])
        for button in (self.beatrice_button, self.pylibrb_button):
            button.setEnabled(not self._addon_busy)
        self.addon_box.setVisible(not (beatrice_ok and present["pylibrb"]))

    def _run_addon(self, label: str, action: Callable[[], str]) -> None:
        self._addon_busy = True
        self._refresh_addons()
        self.state_label.setText(label)

        def worker() -> None:
            try:
                self.bridge.addonFinished.emit(True, action())
            except Exception as error:  # noqa: BLE001 - 利用者に見せる
                log.exception("部品を入れられませんでした")
                self.bridge.addonFinished.emit(False, str(error))

        threading.Thread(target=worker, name="addon", daemon=True).start()

    def _on_beatrice_clicked(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Beatrice の zip を選ぶ", "", "zip (*.zip)")
        if not paths:
            return
        from pathlib import Path

        def action() -> str:
            installed = [addons.install_beatrice_zip(Path(path)) for path in paths]
            return "入れました: " + "、".join(installed)

        self._run_addon("Beatrice を展開しています…", action)

    def _on_pylibrb_clicked(self) -> None:
        self._run_addon("pylibrb を取得しています…", lambda: "入れました: " + addons.install_pylibrb())

    def _on_addon_finished(self, ok: bool, message: str) -> None:
        self._addon_busy = False
        self._refresh_addons()
        self._refresh_view()
        if not ok:
            self._show_error(message)
            return
        self._show_notice(message)
        if self._state in (RUNNING, STARTING) and getattr(self.link, "owned", False):
            # エンジンは起動時に部品を読むので、起動し直す
            self._restart_after_stop = True
            self.link.stop()
        elif self._state in (STOPPED, ERROR):
            self._load_saved_values()

    def _show_notice(self, text: str) -> None:
        self._notice_text = text
        self._refresh_view()

        def clear() -> None:
            if self._notice_text == text:
                self._notice_text = ""
                self._refresh_view()

        QTimer.singleShot(8000, clear)

    def _build_dsp_page(self) -> QWidget:
        page = QGroupBox("ピッチ・フォルマント")
        layout = QVBoxLayout(page)
        presets = QHBoxLayout()
        presets.addWidget(QLabel("プリセット"))
        self.preset_buttons: list[QPushButton] = []
        for preset in self._settings_factory().dsp_presets:
            button = QPushButton(preset["name"])
            button.setToolTip(f"{preset['semitones']:+.1f} 半音、フォルマント ×{preset['formant_factor']:.2f}")
            button.clicked.connect(lambda _checked=False, name=preset["name"]: self._post("/api/dsp", {"preset": name}))
            self.preset_buttons.append(button)
            presets.addWidget(button)
        presets.addStretch(1)
        layout.addLayout(presets)
        self.semitones_slider = ValueSlider("ピッチ", -12.0, 12.0, 0.5,
                                            lambda value: view_state.format_signed(value, 1, " 半音"))
        self.semitones_slider.valueEdited.connect(lambda value: self._post("/api/dsp", {"semitones": value}))
        self.formant_slider = ValueSlider("フォルマント", 0.5, 2.0, 0.01, lambda value: f"×{value:.2f}")
        self.formant_slider.valueEdited.connect(lambda value: self._post("/api/dsp", {"formant_factor": value}))
        layout.addWidget(self.semitones_slider)
        layout.addWidget(self.formant_slider)
        hint = QLabel("フォルマントは 1.00 で元の声の響きのまま。上げると細く、下げると太くなる。")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        layout.addWidget(hint)
        return page

    def _build_ai_page(self) -> QWidget:
        page = QGroupBox("AI（Beatrice 2 公式モデル 1）")
        layout = QVBoxLayout(page)
        voice_row = QHBoxLayout()
        voice_label = QLabel("声")
        voice_label.setMinimumWidth(110)
        self.voice_combo = QComboBox()
        self.voice_combo.activated.connect(self._on_voice_changed)
        voice_row.addWidget(voice_label)
        voice_row.addWidget(self.voice_combo, 1)
        layout.addLayout(voice_row)
        self.pitch_offset_slider = ValueSlider("ピッチの微調整", -12.0, 12.0, 0.125,
                                               lambda value: view_state.format_signed(value, 3, " 半音"))
        self.pitch_offset_slider.valueEdited.connect(lambda value: self._post("/api/ai", {"pitch_offset": value}))
        self.source_pitch_slider = ValueSlider("自分の声の高さ", 30.0, 80.0, 0.125, lambda value: f"{value:.2f}")
        self.source_pitch_slider.valueEdited.connect(
            lambda value: self._post("/api/ai", {"average_source_pitch": value}))
        self.ai_formant_slider = ValueSlider("フォルマント", -2.0, 2.0, 0.5,
                                             lambda value: view_state.format_signed(value, 1, " 半音"))
        self.ai_formant_slider.valueEdited.connect(lambda value: self._post("/api/ai", {"formant_shift": value}))
        self.ai_gain_slider = ValueSlider("AI の出力", -20.0, 20.0, 0.5,
                                          lambda value: view_state.format_signed(value, 1, " dB"))
        self.ai_gain_slider.valueEdited.connect(lambda value: self._post("/api/ai", {"output_gain_db": value}))
        for slider in (self.pitch_offset_slider, self.source_pitch_slider, self.ai_formant_slider, self.ai_gain_slider):
            layout.addWidget(slider)
        self.ai_info_label = QLabel()
        self.ai_info_label.setEnabled(False)
        layout.addWidget(self.ai_info_label)
        terms = QLabel("声ごとの利用規約に従ってください（README の「権利と注意」）。")
        terms.setWordWrap(True)
        terms.setEnabled(False)
        layout.addWidget(terms)
        return page

    # ================================================================ 保存値の表示
    def _load_saved_values(self) -> None:
        """停止中に、保存済みの値を表示する（エンジンが動けばエンジンの値に置き換わる）。"""
        settings = self._settings_factory()
        state = settings.state
        dsp = state.get("dsp", {})
        ai = state.get("ai", {})
        self.semitones_slider.set_value(float(dsp.get("semitones", 0.0)), force=True)
        self.formant_slider.set_value(float(dsp.get("formant_factor", 1.0)), force=True)
        self.gain_slider.set_value(float(state.get("output_gain_db", 0.0)), force=True)
        source_pitch = ai.get("average_source_pitch")
        if source_pitch is None and SELF_PITCH_PATH.is_file():
            try:
                source_pitch = float(json.loads(SELF_PITCH_PATH.read_text(encoding="utf-8"))["midi"])
            except (OSError, KeyError, ValueError):
                source_pitch = None
        self.source_pitch_slider.set_value(float(source_pitch if source_pitch is not None else 52.0), force=True)
        self.pitch_offset_slider.set_value(float(ai.get("pitch_offset", 0.0)), force=True)
        self.ai_formant_slider.set_value(float(ai.get("formant_shift", 0.0)), force=True)
        self.ai_gain_slider.set_value(float(ai.get("output_gain_db", 0.0)), force=True)
        voices = []
        try:
            from ..beatrice_host import read_model_voices

            voices = [{"index": voice.index, "name": voice.name} for voice in read_model_voices(settings.beatrice_model)]
        except Exception:  # noqa: BLE001 - モデルが無ければ空のまま
            voices = []
        self._set_voices(voices, int(ai.get("voice_index", 0)))
        mode = state.get("mode", "mute")
        self._last_panel = "ai" if mode == "ai" else "dsp"
        self.panels.setCurrentIndex(1 if self._last_panel == "ai" else 0)

    def _set_voices(self, voices: list[dict], selected: int) -> None:
        signature = tuple((voice["index"], voice["name"]) for voice in voices)
        if signature != self._voices_signature:
            self.voice_combo.blockSignals(True)
            self.voice_combo.clear()
            for voice in voices:
                self.voice_combo.addItem(voice["name"], voice["index"])
            self.voice_combo.blockSignals(False)
            self._voices_signature = signature
        if not self.voice_combo.view().isVisible():
            position = self.voice_combo.findData(selected)
            if position >= 0:
                self.voice_combo.setCurrentIndex(position)

    # ================================================================ デバイス
    def refresh_devices(self) -> None:
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("読込中…")

        def worker() -> None:
            try:
                self.bridge.devicesLoaded.emit(self._device_lister())
            except Exception as error:  # noqa: BLE001
                self.bridge.devicesFailed.emit(str(error))

        threading.Thread(target=worker, name="device-list", daemon=True).start()

    def _on_devices_loaded(self, document: dict) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("再読込")
        settings = self._settings_factory()
        saved = settings.saved_devices
        configured = settings.config["audio"]
        inputs = list(document.get("inputs", []))
        outputs = list(document.get("outputs", []))
        self._default_output = document.get("default_output")
        current = self.selected_devices() if self._devices_ready else {}
        input_choice = view_state.choose_input(current.get("input_device") or saved.get("input_device"), inputs,
                                               configured.get("input_device"))
        output_choice = view_state.choose_output(current.get("output_device") or saved.get("output_device"), outputs,
                                                 configured.get("monitor_device"))
        monitor_choice = view_state.choose_monitor(current.get("monitor_device") or saved.get("monitor_device"),
                                                   outputs, configured.get("monitor_device"))
        self._populating = True
        try:
            self._fill_combo(self.input_combo, inputs, input_choice)
            self._fill_combo(self.output_combo, outputs, output_choice)
            self._fill_combo(self.monitor_combo, outputs, monitor_choice, with_none=True)
        finally:
            self._populating = False
        self._devices_ready = True
        self._refresh_view()

    def _on_devices_failed(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("再読込")
        self._show_error(message)

    @staticmethod
    def _fill_combo(combo: QComboBox, names: list[str], selected: str | None, with_none: bool = False) -> None:
        combo.blockSignals(True)
        combo.clear()
        if with_none:
            combo.addItem("（なし）", NO_MONITOR)
        for name in names:
            combo.addItem(name, name)
        position = combo.findData(selected) if selected is not None else (0 if with_none else -1)
        combo.setCurrentIndex(max(0, position) if combo.count() else -1)
        combo.blockSignals(False)

    def selected_devices(self) -> dict[str, str | None]:
        return {"input_device": self.input_combo.currentData() or None,
                "output_device": self.output_combo.currentData() or None,
                "monitor_device": self.monitor_combo.currentData() or None}

    def _save_devices(self) -> None:
        """エンジンが止まっている間だけ呼ぶ（設定ファイルを書くのは常に 1 つのプロセスにするため）。"""
        if not self._devices_ready:
            return
        settings = self._settings_factory()
        settings.set_devices(**self.selected_devices())
        settings.save()

    def _on_device_changed(self, _index: int = 0) -> None:
        if self._populating:
            return
        if self._state in (RUNNING, STARTING):
            self._restart_after_stop = True
            self.link.stop()
        elif self._state in (STOPPED, ERROR):
            self._save_devices()
        self._refresh_view()

    # ================================================================ 起動・停止
    def _attach_existing(self) -> None:
        try:
            self.link.attach_existing()
        except Exception as error:  # noqa: BLE001
            log.warning("起動済みのエンジンに接続できませんでした: %s", error)

    def _on_start_clicked(self) -> None:
        if self._state in (STOPPED, ERROR):
            self.start_engine()
        elif self._state in (RUNNING, STARTING):
            self._restart_after_stop = False
            self.link.stop()

    def start_engine(self) -> None:
        self._error_text = ""
        self._save_devices()
        self.link.start(self.engine_arguments)

    def _open_log(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ENGINE_LOG)))

    # ================================================================ 操作
    def _post(self, path: str, body: dict) -> None:
        if self._state == RUNNING:
            self.link.post(path, body)

    def _on_mode_clicked(self, mode: str) -> None:
        if mode in ("dsp", "ai"):
            self._last_panel = mode
            self.panels.setCurrentIndex(1 if mode == "ai" else 0)
        self._post("/api/mode", {"mode": mode})
        self._refresh_mode_buttons()  # 押したボタンが仮に選ばれるだけで、実際の状態はエンジンの応答で決まる

    def _on_voice_changed(self, _index: int) -> None:
        voice_index = self.voice_combo.currentData()
        if voice_index is not None:
            self._post("/api/ai", {"voice_index": int(voice_index)})

    def _on_monitor_clicked(self, checked: bool) -> None:
        self._post("/api/monitor", {"enabled": bool(checked)})

    # ================================================================ エンジンからの通知
    def _on_state(self, state: str, message: str) -> None:
        self._state = state
        self._state_message = message
        if state == ERROR:
            self._error_text = message
        if state in (STOPPED, ERROR):
            self._status = None
            self._load_saved_values()
            if state == STOPPED and self._restart_after_stop:
                self._restart_after_stop = False
                QTimer.singleShot(0, self.start_engine)
        self._refresh_view()

    def _on_status(self, status: dict) -> None:
        if self._state != RUNNING:
            return
        self._status = status
        self._refresh_view()

    def _on_command_finished(self, path: str, result: Any, error: Any) -> None:
        if error is not None:
            self._show_error(str(error))
            return
        if isinstance(result, dict) and isinstance(result.get("status"), dict) and self._state == RUNNING:
            self._status = {**(self._status or {}), **result["status"]}
            self._refresh_view()

    def _show_error(self, text: str) -> None:
        self._error_text = text
        self._refresh_view()

        def clear() -> None:
            if self._error_text == text and self._state != ERROR:
                self._error_text = ""
                self._refresh_view()

        QTimer.singleShot(8000, clear)

    # ================================================================ 表示の更新
    def _refresh_mode_buttons(self) -> None:
        status = self._status or {}
        available = set(status.get("available_modes", []))
        running = self._state == RUNNING and bool(status)
        for mode, button in self.mode_buttons.items():
            button.setEnabled(running and mode in available)
            reason = (status.get("unavailable") or {}).get(mode)
            button.setToolTip(f"使えません: {reason}" if reason else "")
        current = status.get("mode") if running else None
        self.mode_group.setExclusive(False)
        for mode, button in self.mode_buttons.items():
            button.setChecked(mode == current)
            pending = running and status.get("pending_mode") == mode
            button.setText(view_state.MODE_LABELS[mode] + ("（切替中）" if pending else ""))
        self.mode_group.setExclusive(True)

    def _refresh_view(self) -> None:
        label, kind = view_state.state_label(self._state)
        self.state_dot.set_kind(kind)
        message = self._state_message if self._state in (STARTING, STOPPING) else ""
        self.state_label.setText(label + (f"  {message}" if message else ""))
        busy = self._state in (STARTING, STOPPING)
        self.start_button.setText("■ 停止" if self._state in (RUNNING, STARTING) else "▶ 開始")
        self.start_button.setEnabled(self._state != STOPPING and (self._devices_ready or self._state != STOPPED))
        self.log_button.setVisible(self._state == ERROR)
        for combo in (self.input_combo, self.output_combo, self.monitor_combo):
            combo.setEnabled(self._devices_ready and not busy)
        self.refresh_button.setEnabled(not busy and self.refresh_button.text() == "再読込")

        status = self._status if self._state == RUNNING else None
        running = status is not None
        self._refresh_mode_buttons()
        for widget in (self.panels, self.gain_slider):
            widget.setEnabled(running)
        if running:
            self._apply_status(status)
        else:
            self.latency_label.setText("")
            self.input_meter.set_level(None)
            self.output_meter.set_level(None)
            self.input_db_label.setText("-∞ dB")
            self.output_db_label.setText("-∞ dB")
            self.monitor_check.setChecked(False)
            self.monitor_check.setEnabled(False)
            for label_widget in self.stats_labels.values():
                label_widget.setText("-")

        items: list[tuple[str, str]] = []
        if self._error_text:
            items.append(("error", self._error_text))
        if self._notice_text:
            items.append(("info", self._notice_text))
        render_warning = view_state.default_render_warning(self._default_output)
        if render_warning:
            items.append(("danger", render_warning))
        input_name = None
        if status:
            input_name = ((status.get("backend") or {}).get("devices") or {}).get("input")
        loop_warning = view_state.input_loop_warning(input_name or self.input_combo.currentData())
        if loop_warning:
            items.append(("danger", loop_warning))
        output_name = None
        if status:
            output_name = ((status.get("backend") or {}).get("devices") or {}).get("output")
        if output_name is None:
            output_name = self.output_combo.currentData()
        for warning in view_state.warnings_from_status(status, output_name):
            items.append((warning.level, warning.text))
        hint = view_state.output_route_hint(output_name) if not status and self._devices_ready else None
        if hint:
            items.append(("info", hint))
        self.banners.set_items(items)

    def _apply_status(self, status: dict) -> None:
        mode = status.get("mode")
        if mode in ("dsp", "ai"):
            self._last_panel = mode
        self.panels.setCurrentIndex(1 if self._last_panel == "ai" else 0)
        self.latency_label.setText(f"遅延 {status.get('aligned_latency_ms', 0):.0f} ms")
        processors = status.get("processors") or {}
        dsp = processors.get("dsp")
        self.panels.widget(0).setEnabled(dsp is not None)
        if dsp:
            self.semitones_slider.set_value(dsp["semitones"])
            self.formant_slider.set_value(dsp["formant_factor"])
        ai = processors.get("ai")
        self.panels.widget(1).setEnabled(ai is not None)
        if ai:
            self._set_voices(ai.get("voices", []), int(ai.get("voice_index", 0)))
            self.pitch_offset_slider.set_value(ai["pitch_offset"])
            self.source_pitch_slider.set_value(ai["average_source_pitch"])
            self.ai_formant_slider.set_value(ai["formant_shift"])
            self.ai_gain_slider.set_value(ai["output_gain_db"])
            self.ai_info_label.setText(f"VST に渡しているピッチシフト: {ai['pitch_shift']:+.3f} 半音")
        self.gain_slider.set_value(status.get("output_gain_db", 0.0))
        levels = status.get("levels") or {}
        self.input_meter.set_level(levels.get("input_peak_db"))
        self.output_meter.set_level(levels.get("output_peak_db"))
        self.input_db_label.setText(view_state.format_db(levels.get("input_peak_db")))
        self.output_db_label.setText(view_state.format_db(levels.get("output_peak_db")))
        monitor_ok = view_state.monitor_available(status)
        self.monitor_check.setEnabled(monitor_ok)
        self.monitor_check.setChecked(bool(status.get("monitor")))
        self.monitor_check.setToolTip("" if monitor_ok else "モニターのデバイスが無いか、出力と同じです")
        for name, value in view_state.stats_rows(status):
            if name in self.stats_labels:
                self.stats_labels[name].setText(value)

    # ================================================================ 終了
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt の名前
        if self._state in (RUNNING, STARTING) and getattr(self.link, "owned", False):
            self.state_label.setText("エンジンを止めています…")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            try:
                self.link.close()
            finally:
                QApplication.restoreOverrideCursor()
        else:
            self.link.close()
        event.accept()
