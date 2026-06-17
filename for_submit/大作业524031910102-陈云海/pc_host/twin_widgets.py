from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets


def encode_char(value: str) -> int:
    table = {
        "0": 0x3F,
        "1": 0x06,
        "2": 0x5B,
        "3": 0x4F,
        "4": 0x66,
        "5": 0x6D,
        "6": 0x7D,
        "7": 0x07,
        "8": 0x7F,
        "9": 0x6F,
        "A": 0x77,
        "B": 0x7C,
        "C": 0x39,
        "D": 0x5E,
        "E": 0x79,
        "F": 0x71,
        "G": 0x3D,
        "H": 0x76,
        "I": 0x06,
        "J": 0x1E,
        "K": 0x76,
        "L": 0x38,
        "M": 0x37,
        "N": 0x54,
        "O": 0x3F,
        "P": 0x73,
        "Q": 0x67,
        "R": 0x50,
        "S": 0x6D,
        "T": 0x78,
        "U": 0x3E,
        "V": 0x3E,
        "W": 0x3E,
        "X": 0x76,
        "Y": 0x6E,
        "Z": 0x5B,
        "-": 0x40,
        "_": 0x08,
        " ": 0x00,
    }
    return table.get(value.upper(), 0x00)


class SevenSegmentDisplayWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = "        "
        self._dp_mask = 0
        self.setMinimumHeight(62)
        self.setMaximumHeight(78)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(420, 72)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(320, 62)

    def set_frame(self, token: str, dp_mask: int) -> None:
        self._text = token[:8].ljust(8, " ")
        self._dp_mask = dp_mask & 0xFF
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#0e1520"))

        margin = 5
        inner = self.rect().adjusted(margin, margin, -margin, -margin)
        digit_width = inner.width() / 8.0
        on_color = QtGui.QColor("#e85d2a")
        off_color = QtGui.QColor("#2a3746")

        for index, ch in enumerate(self._text):
            digit_rect = QtCore.QRectF(
                inner.left() + index * digit_width + 2,
                inner.top(),
                digit_width - 4,
                inner.height(),
            )
            self._draw_digit(
                painter,
                digit_rect,
                encode_char(ch),
                bool(self._dp_mask & (1 << index)),
                on_color,
                off_color,
            )

    def _draw_segment(
        self,
        painter: QtGui.QPainter,
        points: list[QtCore.QPointF],
        enabled: bool,
        on_color: QtGui.QColor,
        off_color: QtGui.QColor,
    ) -> None:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(on_color if enabled else off_color)
        painter.drawPolygon(QtGui.QPolygonF(points))

    def _draw_digit(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        segments: int,
        dp_enabled: bool,
        on_color: QtGui.QColor,
        off_color: QtGui.QColor,
    ) -> None:
        w = rect.width()
        h = rect.height()
        thickness = max(3.2, min(w, h) * 0.105)
        slant = thickness * 0.35

        x0, y0 = rect.left(), rect.top()
        x1, y1 = rect.right(), rect.bottom()
        mid = (y0 + y1) / 2.0

        def horiz(y: float) -> list[QtCore.QPointF]:
            return [
                QtCore.QPointF(x0 + slant + thickness, y),
                QtCore.QPointF(x1 - slant - thickness, y),
                QtCore.QPointF(x1 - slant, y + thickness / 2),
                QtCore.QPointF(x1 - slant - thickness, y + thickness),
                QtCore.QPointF(x0 + slant + thickness, y + thickness),
                QtCore.QPointF(x0 + slant, y + thickness / 2),
            ]

        def vert(x: float, top: float, bottom: float) -> list[QtCore.QPointF]:
            return [
                QtCore.QPointF(x, top + slant),
                QtCore.QPointF(x + thickness / 2, top),
                QtCore.QPointF(x + thickness, top + slant),
                QtCore.QPointF(x + thickness, bottom - slant),
                QtCore.QPointF(x + thickness / 2, bottom),
                QtCore.QPointF(x, bottom - slant),
            ]

        segment_geometries = [
            horiz(y0),
            vert(x1 - thickness, y0 + thickness * 0.6, mid - thickness * 0.6),
            vert(x1 - thickness, mid + thickness * 0.2, y1 - thickness * 1.3),
            horiz(y1 - thickness),
            vert(x0, mid + thickness * 0.2, y1 - thickness * 1.3),
            vert(x0, y0 + thickness * 0.6, mid - thickness * 0.6),
            horiz(mid - thickness / 2),
        ]

        for index, geometry in enumerate(segment_geometries):
            self._draw_segment(
                painter,
                geometry,
                bool(segments & (1 << index)),
                on_color,
                off_color,
            )

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(on_color if dp_enabled else off_color)
        dot_radius = thickness * 0.46
        painter.drawEllipse(
            QtCore.QPointF(x1 - dot_radius * 1.3, y1 - dot_radius * 1.6),
            dot_radius,
            dot_radius,
        )


class LedBarWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0
        self.setMinimumHeight(24)
        self.setMaximumHeight(30)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(420, 28)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(320, 24)

    def set_led_byte(self, value: int) -> None:
        self._value = value & 0xFF
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#13202f"))
        margin = 12
        diameter = min(16, max(9, (self.width() - margin * 2) // 14))
        gap = diameter * 0.55
        total = diameter * 8 + gap * 7
        start_x = (self.width() - total) / 2.0
        center_y = self.height() * 0.40

        for index in range(8):
            active = bool(self._value & (1 << index))
            painter.setBrush(
                QtGui.QColor("#49c16d") if active else QtGui.QColor("#2a3746")
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#d8e1ea"), 1))
            x = start_x + index * (diameter + gap)
            painter.drawEllipse(
                QtCore.QRectF(x, center_y - diameter / 2.0, diameter, diameter)
            )
            painter.setPen(QtGui.QColor("#cbd5df"))
            painter.setFont(QtGui.QFont("Consolas", 7))
            painter.drawText(
                QtCore.QRectF(x, center_y + diameter / 2.0 + 1, diameter, 14),
                QtCore.Qt.AlignCenter,
                str(index + 1),
            )


class DigitalTwinWidget(QtWidgets.QWidget):
    virtual_key_requested = QtCore.pyqtSignal(str)
    virtual_key_long_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(5)
        outer.setContentsMargins(0, 0, 0, 0)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.header_label = QtWidgets.QLabel("数字孪生镜像")
        self.header_label.setObjectName("twinHeaderLabel")
        self.header_label.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.title_label = QtWidgets.QLabel("8x7SEG + LED + KEY")
        self.title_label.setObjectName("twinTitle")
        self.title_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored,
            QtWidgets.QSizePolicy.Fixed,
        )
        header_layout.addWidget(self.header_label)
        header_layout.addWidget(self.title_label, 1)
        outer.addLayout(header_layout)

        self.display = SevenSegmentDisplayWidget(self)
        outer.addWidget(self.display)

        self.leds = LedBarWidget(self)
        self.leds.setToolTip(
            "LED 位义：D1心跳，D2闹钟，D3编辑，D4串口RX，D5串口TX，"
            "D6夜间，D7RIGHT显示，D8 NTP同步；天气短显/LED掩码会临时覆盖整组 LED"
        )
        outer.addWidget(self.leds)

        self.grid = QtWidgets.QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4)

        self.key_buttons: list[QtWidgets.QPushButton] = []
        self.key_button_by_name: dict[str, QtWidgets.QPushButton] = {}
        self._key_press_timers: dict[QtWidgets.QPushButton, QtCore.QTimer] = {}
        self._key_long_fired: set[QtWidgets.QPushButton] = set()
        self.key_button_base_style = "padding: 2px 4px;"
        key_tips = {
            "USER2": "USER2: 天气短显专用键；有缓存直接显示天气，无缓存时显示 NO WX",
            "EXT": "SW8 / EXT: 退出编辑、取消天气短显或滚动消息；在 ALARM 编辑页或正常页无临时画面时关闭单次闹钟",
            "FORMAT": "SW7 / FORMAT: 切换 LEFT/RIGHT 数码管显示方向",
            "SPEED": "SW6 / SPEED: 切换走马灯快/慢速度",
            "DISP": "SW5 / DISP: 切换时间、日期、星期、年份页面；长按关显示和 LED",
            "USER1": "USER1: 短按请求 PC 对时；长按切换 DAY/NIGHT",
            "FUNC": "SW1 / FUNC: 进入编辑；长按保存当前编辑",
            "SHIFT": "SW2 / SHIFT: 编辑时切换字段",
            "ADD": "SW3 / ADD: 编辑时加一，长按连加",
            "SAVE": "SW4 / SAVE: 保存编辑值",
        }
        keys = [
            ("USER2", "USER2\nWX", 0, 0),
            ("EXT", "SW8\nEXT", 0, 1),
            ("FORMAT", "SW7\nFMT", 0, 2),
            ("SPEED", "SW6\nSPD", 0, 3),
            ("DISP", "SW5\nDISP", 0, 4),
            ("USER1", "USER1\nNTP", 1, 0),
            ("FUNC", "SW1\nFUNC", 1, 1),
            ("SHIFT", "SW2\nSHIFT", 1, 2),
            ("ADD", "SW3\nADD", 1, 3),
            ("SAVE", "SW4\nSAVE", 1, 4),
        ]
        for key_name, label, row, column in keys:
            button = QtWidgets.QPushButton(label)
            button.setObjectName("twinKeyButton")
            button.setToolTip(key_tips[key_name])
            button.setMinimumHeight(28)
            button.setMaximumHeight(32)
            button.setMinimumWidth(0)
            button.setStyleSheet(self.key_button_base_style)
            font = button.font()
            font.setPointSize(6)
            font.setBold(True)
            button.setFont(font)
            button.pressed.connect(
                lambda value=key_name, button=button: self._start_key_press(value, button)
            )
            button.released.connect(
                lambda value=key_name, button=button: self._finish_key_press(value, button)
            )
            self.key_buttons.append(button)
            self.key_button_by_name[key_name] = button
            self.grid.addWidget(button, row, column)

        for column in range(5):
            self.grid.setColumnStretch(column, 1)

        outer.addLayout(self.grid)

    def _start_key_press(self, key_name: str, button: QtWidgets.QPushButton) -> None:
        self._key_long_fired.discard(button)
        timer = self._key_press_timers.get(button)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            self._key_press_timers[button] = timer
        try:
            timer.timeout.disconnect()
        except TypeError:
            pass
        timer.timeout.connect(lambda key=key_name, button=button: self._emit_key_long(key, button))
        timer.start(800)

    def _emit_key_long(self, key_name: str, button: QtWidgets.QPushButton) -> None:
        self._key_long_fired.add(button)
        self.virtual_key_long_requested.emit(key_name)

    def _finish_key_press(self, key_name: str, button: QtWidgets.QPushButton) -> None:
        timer = self._key_press_timers.get(button)
        if timer is not None and timer.isActive():
            timer.stop()
        if button in self._key_long_fired:
            self._key_long_fired.discard(button)
            return
        self.virtual_key_requested.emit(key_name)

    def sizeHint(self) -> QtCore.QSize:
        self.ensurePolished()
        layout = self.layout()
        margins = layout.contentsMargins()
        button_height = max(
            (button.sizeHint().height() for button in self.key_buttons),
            default=28,
        )
        total_height = (
            margins.top()
            + margins.bottom()
            + max(self.header_label.sizeHint().height(), self.title_label.sizeHint().height())
            + self.display.sizeHint().height()
            + self.leds.sizeHint().height()
            + button_height * 2
            + self.grid.verticalSpacing()
            + layout.spacing() * 3
            + 4
        )
        return QtCore.QSize(420, total_height + 6)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(320, self.sizeHint().height())

    def set_display_frame(self, token: str, dp_mask: int) -> None:
        self.display.set_frame(token, dp_mask)

    def set_led_byte(self, value: int) -> None:
        self.leds.set_led_byte(value)

    def highlight_key(self, key_name: str, duration_ms: int = 200) -> None:
        key = key_name.strip().upper()
        button = self.key_button_by_name.get(key)
        if button is None:
            return
        button.setStyleSheet(
            self.key_button_base_style
            + " background-color: #ffd166; color: #101820; border: 1px solid #ffb703;"
        )
        QtCore.QTimer.singleShot(
            duration_ms,
            lambda key=key, button=button: self._clear_key_highlight(key, button),
        )

    def _clear_key_highlight(self, key: str, button: QtWidgets.QPushButton) -> None:
        if self.key_button_by_name.get(key) is button:
            button.setStyleSheet(self.key_button_base_style)
