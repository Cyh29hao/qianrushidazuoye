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
        self._text = "________"
        self._dp_mask = 0
        self.setMinimumHeight(92)
        self.setMaximumHeight(118)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(820, 108)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(460, 92)

    def set_frame(self, token: str, dp_mask: int) -> None:
        self._text = token[:8].ljust(8, "_")
        self._dp_mask = dp_mask & 0xFF
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#0e1520"))

        margin = 7
        inner = self.rect().adjusted(margin, margin, -margin, -margin)
        digit_width = inner.width() / 8.0
        on_color = QtGui.QColor("#e85d2a")
        off_color = QtGui.QColor("#2a3746")

        for index, ch in enumerate(self._text):
            digit_rect = QtCore.QRectF(
                inner.left() + index * digit_width + 3,
                inner.top(),
                digit_width - 6,
                inner.height(),
            )
            self._draw_digit(
                painter,
                digit_rect,
                encode_char(" " if ch == "_" else ch),
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
        thickness = max(4.0, min(w, h) * 0.10)
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
        self.setMinimumHeight(30)
        self.setMaximumHeight(38)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(820, 34)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(460, 30)

    def set_led_byte(self, value: int) -> None:
        self._value = value & 0xFF
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#13202f"))
        margin = 18
        diameter = min(22, max(12, (self.width() - margin * 2) // 12))
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
            painter.setFont(QtGui.QFont("Consolas", 8))
            painter.drawText(
                QtCore.QRectF(x, center_y + diameter / 2.0 + 1, diameter, 14),
                QtCore.Qt.AlignCenter,
                str(index + 1),
            )


class DigitalTwinWidget(QtWidgets.QWidget):
    virtual_key_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(6)
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
        self.title_label = QtWidgets.QLabel("8x7SEG + 8 LED + 8 按键 + USER1 / USER2")
        self.title_label.setObjectName("twinTitle")
        self.title_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.title_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        header_layout.addWidget(self.header_label)
        header_layout.addWidget(self.title_label, 1)
        outer.addLayout(header_layout)

        self.display = SevenSegmentDisplayWidget(self)
        outer.addWidget(self.display)

        self.leds = LedBarWidget(self)
        outer.addWidget(self.leds)

        self.grid = QtWidgets.QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(7)
        self.grid.setVerticalSpacing(6)

        self.key_buttons: list[QtWidgets.QPushButton] = []
        self.key_button_by_name: dict[str, QtWidgets.QPushButton] = {}
        self.key_button_base_style = "padding: 2px 4px;"
        keys = [
            ("USER2", "SW2\nUSER2/SUB", 0, 0),
            ("EXT", "SW8\nEXT", 0, 1),
            ("FORMAT", "SW7\nFORMAT", 0, 2),
            ("SPEED", "SW6\nSPEED", 0, 3),
            ("DISP", "SW5\nDISP", 0, 4),
            ("USER1", "SW1\nUSER1/NTP", 1, 0),
            ("FUNC", "SW1\nFUNC", 1, 1),
            ("SHIFT", "SW2\nSHIFT", 1, 2),
            ("ADD", "SW3\nADD", 1, 3),
            ("SAVE", "SW4\nSAVE", 1, 4),
        ]
        for key_name, label, row, column in keys:
            button = QtWidgets.QPushButton(label)
            button.setObjectName("twinKeyButton")
            if key_name == "USER1":
                button.setToolTip("短按请求 PC 对时；板端长按切换 DAY/NIGHT")
            if key_name == "USER2":
                button.setToolTip("USER2: 非编辑状态显示天气短显；编辑状态作为 SUB 减一键")
            button.setMinimumHeight(38)
            button.setMaximumHeight(44)
            button.setStyleSheet(self.key_button_base_style)
            font = button.font()
            font.setPointSize(8)
            font.setBold(True)
            button.setFont(font)
            button.clicked.connect(
                lambda checked=False, value=key_name: self.virtual_key_requested.emit(value)
            )
            self.key_buttons.append(button)
            self.key_button_by_name[key_name] = button
            self.grid.addWidget(button, row, column)

        for column in range(5):
            self.grid.setColumnStretch(column, 1)

        outer.addLayout(self.grid)

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
        return QtCore.QSize(760, total_height)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(460, self.sizeHint().height())

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
