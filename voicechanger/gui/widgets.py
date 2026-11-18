"""画面の部品: 値つきスライダ、レベルメーター、警告の帯、アイコン。"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSlider, QVBoxLayout, QWidget

from . import view_state

EDIT_HOLD_SECONDS = 0.8  # 操作のあと、エンジンからの値で上書きしない時間


def is_dark(widget: QWidget) -> bool:
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


class ValueSlider(QWidget):
    """名前・スライダ・値の 1 行。動かしている間も 0.1 秒おきに valueEdited を出す。"""

    valueEdited = Signal(float)

    def __init__(self, label: str, minimum: float, maximum: float, step: float,
                 formatter: Callable[[float], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = minimum
        self._maximum = maximum
        self._step = step
        self._formatter = formatter
        self._hold_until = 0.0
        self._updating = False
        self.name_label = QLabel(label)
        self.name_label.setMinimumWidth(110)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, round((maximum - minimum) / step))
        self.slider.setSingleStep(1)
        self.slider.setPageStep(max(1, round(1.0 / step)))
        self.slider.setAccessibleName(label)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(92)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.addWidget(self.name_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._emit_current)
        self.slider.valueChanged.connect(self._on_changed)
        self.slider.sliderReleased.connect(self._on_released)
        self._update_label()

    def value(self) -> float:
        return round(self._minimum + self.slider.value() * self._step, 6)

    def is_editing(self) -> bool:
        return self.slider.isSliderDown() or time.monotonic() < self._hold_until

    def set_value(self, value: float, force: bool = False) -> None:
        """エンジンや保存値からの値を表示する。操作中は無視する（force で強制）。"""
        if not force and self.is_editing():
            return
        clamped = min(self._maximum, max(self._minimum, float(value)))
        self._updating = True
        self.slider.setValue(round((clamped - self._minimum) / self._step))
        self._updating = False
        self._update_label()

    def _update_label(self) -> None:
        self.value_label.setText(self._formatter(self.value()))

    def _on_changed(self, _position: int) -> None:
        self._update_label()
        if self._updating:
            return
        self._hold_until = time.monotonic() + EDIT_HOLD_SECONDS
        if not self._timer.isActive():
            self._timer.start()

    def _on_released(self) -> None:
        self._timer.stop()
        self._emit_current()

    def _emit_current(self) -> None:
        self._hold_until = time.monotonic() + EDIT_HOLD_SECONDS
        self.valueEdited.emit(self.value())


class LevelMeter(QWidget):
    COLORS = {"normal": QColor("#3b9b5e"), "hot": QColor("#d9822b"), "clip": QColor("#c62f2f")}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._decibels: float | None = None
        self.setMinimumHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, decibels: float | None) -> None:
        if decibels != self._decibels:
            self._decibels = decibels
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt の名前
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        background = QColor("#3a3f47") if is_dark(self) else QColor("#e3e6eb")
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, background)
        fraction = view_state.meter_fraction(self._decibels)
        if fraction > 0:
            bar = QRectF(rect.left(), rect.top(), rect.width() * fraction, rect.height())
            bar_path = QPainterPath()
            bar_path.addRoundedRect(bar, 4, 4)
            painter.fillPath(bar_path, self.COLORS[view_state.meter_color(self._decibels)])
        painter.end()


class Banner(QLabel):
    """警告の帯。level は danger / info / error。"""

    def __init__(self, text: str, level: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.level = level
        self.apply_style()

    def apply_style(self) -> None:
        dark = is_dark(self)
        if self.level in ("danger", "error"):
            background, foreground = ("#3a1d20", "#ff9b94") if dark else ("#fdecec", "#a8201a")
            weight = "600"
        else:
            background, foreground = ("#26303d", "#c9d4e3") if dark else ("#eef2f7", "#3b4656")
            weight = "400"
        self.setStyleSheet(f"QLabel {{ background: {background}; color: {foreground}; border-radius: 6px; "
                           f"padding: 6px 10px; font-weight: {weight}; }}")


class BannerArea(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._current: list[tuple[str, str]] = []

    def set_items(self, items: list[tuple[str, str]]) -> None:
        if items == self._current:
            return
        self._current = list(items)
        while self._layout.count():
            widget = self._layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for level, text in items:
            self._layout.addWidget(Banner(text, level, self))
        self.setVisible(bool(items))

    def texts(self) -> list[str]:
        return [text for _level, text in self._current]


class StatusDot(QLabel):
    COLORS = {"idle": "#8a94a6", "busy": "#d9822b", "ok": "#2f9e57", "error": "#d03a32"}

    def set_kind(self, kind: str) -> None:
        self.setText("●")
        self.setStyleSheet(f"QLabel {{ color: {self.COLORS.get(kind, '#8a94a6')}; font-size: 16px; }}")


def make_app_icon(size: int = 256) -> QIcon:
    """アプリのアイコン（角丸の四角に音の波形）。外部の画像は使わない。"""
    icon = QIcon()
    for edge in (16, 24, 32, 48, 64, 128, size):
        icon.addPixmap(_draw_icon(edge))
    return icon


def _draw_icon(edge: int) -> QPixmap:
    pixmap = QPixmap(edge, edge)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = edge * 0.06
    rect = QRectF(margin, margin, edge - 2 * margin, edge - 2 * margin)
    path = QPainterPath()
    path.addRoundedRect(rect, edge * 0.22, edge * 0.22)
    painter.fillPath(path, QColor("#2f6fde"))
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(max(1.5, edge * 0.075))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    heights = (0.22, 0.42, 0.62, 0.38, 0.52, 0.26)
    count = len(heights)
    for index, height in enumerate(heights):
        x = rect.left() + rect.width() * (index + 1) / (count + 1)
        half = rect.height() * height / 2
        center = rect.center().y()
        painter.drawLine(QPointF(x, center - half), QPointF(x, center + half))
    painter.end()
    return pixmap
