from __future__ import annotations

import html
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from bootstrap_qt import configure_qt_runtime

APP_DIR = Path(__file__).resolve().parent
QT_RUNTIME = configure_qt_runtime(APP_DIR)

import serial
from PyQt5 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from protocol import (
    ParsedLine,
    build_set_date_command,
    build_set_time_command,
    parse_line,
)
from twin_widgets import DigitalTwinWidget
from ui_main import Ui_MainWindow


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.serial_port: serial.Serial | None = None
        self.read_buffer = ""
        self.pending_queries: deque[str] = deque()
        self.last_ping_monotonic: float | None = None
        self.last_mode = "DAY"
        self.last_alarm = "OFF"
        self.cached_weather_text = ""
        self.last_display_event: tuple[str, int] | None = None
        self.last_led_event: int | None = None
        self.latest_display_text = "--"
        self.latest_led_text = "--"
        self.latest_event_text = "等待数据"
        self.max_log_blocks = 400
        self.sync_in_progress = False
        self.sync_snapshot: datetime | None = None

        self.twin = DigitalTwinWidget(self)
        twin_layout = QtWidgets.QVBoxLayout(self.ui.twinContainer)
        twin_layout.setContentsMargins(0, 0, 0, 0)
        twin_layout.addWidget(self.twin)

        self._build_statusbar()
        self._apply_theme()
        self._prepare_widgets()
        self._wire_signals()
        self._refine_layout()

        self.port_timer = QtCore.QTimer(self)
        self.port_timer.setInterval(1500)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(20)
        self.poll_timer.timeout.connect(self.poll_serial)

        self.ping_timer = QtCore.QTimer(self)
        self.ping_timer.setInterval(2000)
        self.ping_timer.timeout.connect(self.send_ping)

        self.refresh_ports()
        self.log("INFO", "PC 上位机已启动，等待连接 S800。")

    def _build_statusbar(self) -> None:
        self.status_connection = QtWidgets.QLabel("连接: 未连接")
        self.status_format = QtWidgets.QLabel("FORMAT: LEFT")
        self.status_mode = QtWidgets.QLabel("MODE: DAY")
        self.status_alarm = QtWidgets.QLabel("ALARM: OFF")
        self.status_latency = QtWidgets.QLabel("延迟: -- ms")
        self.ui.statusbar.addPermanentWidget(self.status_connection)
        self.ui.statusbar.addPermanentWidget(self.status_format)
        self.ui.statusbar.addPermanentWidget(self.status_mode)
        self.ui.statusbar.addPermanentWidget(self.status_alarm)
        self.ui.statusbar.addPermanentWidget(self.status_latency)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f4efe7;
            }
            QWidget {
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
                color: #16324f;
            }
            QGroupBox {
                background: #fffdfa;
                border: 1px solid #d7d0c6;
                border-radius: 10px;
                margin-top: 12px;
                font-weight: 700;
            }
            QGroupBox::title {
                left: 12px;
                padding: 0 6px;
                color: #123b63;
            }
            QPushButton {
                background: #1f5b8c;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 5px 10px;
                font-weight: 600;
                min-height: 30px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2b76b3;
            }
            QPushButton:pressed {
                background: #174969;
            }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QTextEdit {
                background: #ffffff;
                border: 1px solid #c5d0da;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {
                min-height: 32px;
            }
            QTextEdit {
                font-family: "Consolas";
                font-size: 11px;
            }
            QLabel#twinTitle {
                font-size: 10px;
                font-weight: 600;
                color: #0f3d60;
            }
            QLabel#twinHeaderLabel {
                font-size: 12px;
                font-weight: 700;
                color: #123b63;
            }
            QLabel.infoChip {
                background: #eef5fb;
                border: 1px solid #c5d7e8;
                border-radius: 8px;
                padding: 4px 8px;
                color: #234a70;
                font-size: 11px;
            }
            QCheckBox {
                spacing: 6px;
                font-size: 11px;
            }
            QTabWidget::pane {
                border: 1px solid #d7d0c6;
                border-radius: 10px;
                background: #fffdfa;
                top: -1px;
            }
            QTabBar::tab {
                background: #e5edf5;
                border: 1px solid #c5d0da;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 6px 12px;
                min-width: 90px;
                color: #214d77;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #fffdfa;
            }
            QSplitter::handle {
                background: #d9d0c3;
                border-radius: 3px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            """
        )

    def _refine_layout_legacy(self) -> None:
        self.resize(1680, 940)
        self.setMinimumSize(1440, 860)
        self.ui.horizontalLayout.setContentsMargins(18, 18, 18, 18)
        self.ui.horizontalLayout.setSpacing(18)
        while self.ui.horizontalLayout.count():
            item = self.ui.horizontalLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for group in (
            self.ui.connectionGroup,
            self.ui.clockGroup,
            self.ui.displayGroup,
            self.ui.demoGroup,
            self.ui.twinGroup,
            self.ui.logGroup,
        ):
            group.setParent(None)

        leftPane = QtWidgets.QScrollArea(self.ui.centralwidget)
        leftPane.setWidgetResizable(True)
        leftPane.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        leftPane.setFrameShape(QtWidgets.QFrame.NoFrame)
        leftPane.setMinimumWidth(620)
        leftPane.setMaximumWidth(690)

        leftHost = QtWidgets.QWidget()
        leftLayout = QtWidgets.QVBoxLayout(leftHost)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(14)
        leftLayout.addWidget(self.ui.connectionGroup)
        leftLayout.addWidget(self.ui.clockGroup)
        leftLayout.addWidget(self.ui.displayGroup)
        leftLayout.addWidget(self.ui.demoGroup)
        leftLayout.addStretch(1)
        leftPane.setWidget(leftHost)

        rightPanel = QtWidgets.QWidget(self.ui.centralwidget)
        rightPanel.setMinimumWidth(1080)
        rightPanel.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        rightLayout = QtWidgets.QVBoxLayout(rightPanel)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(14)
        rightLayout.addWidget(self.ui.twinGroup, 6)
        rightLayout.addWidget(self.ui.logGroup, 4)

        self.ui.connectionGroup.setMinimumHeight(150)
        self.ui.clockGroup.setMinimumHeight(250)
        self.ui.displayGroup.setMinimumHeight(220)
        self.ui.demoGroup.setMinimumHeight(150)
        self.ui.twinGroup.setMinimumHeight(600)
        self.ui.logGroup.setMinimumHeight(300)

        self.ui.horizontalLayout.addWidget(leftPane, 8)
        self.ui.horizontalLayout.addWidget(rightPanel, 12)

        self.ui.logTextEdit.setMinimumHeight(220)
        self.ui.logTextEdit.document().setMaximumBlockCount(self.max_log_blocks)

        log_layout = self.ui.logGroup.layout()
        if log_layout is not None and not hasattr(self, "latestDisplayLabel"):
            summary_widget = QtWidgets.QWidget(self.ui.logGroup)
            summary_layout = QtWidgets.QGridLayout(summary_widget)
            summary_layout.setContentsMargins(0, 0, 0, 0)
            summary_layout.setHorizontalSpacing(10)
            summary_layout.setVerticalSpacing(6)
            summary_layout.setColumnStretch(0, 3)
            summary_layout.setColumnStretch(1, 3)
            summary_layout.setColumnStretch(2, 1)
            summary_layout.setColumnStretch(3, 1)

            self.latestDisplayLabel = QtWidgets.QLabel("最新显示: --", summary_widget)
            self.latestDisplayLabel.setObjectName("latestDisplayLabel")
            self.latestDisplayLabel.setProperty("class", "infoChip")
            self.latestDisplayLabel.setStyleSheet("")

            self.latestLedLabel = QtWidgets.QLabel("最新 LED: --", summary_widget)
            self.latestLedLabel.setObjectName("latestLedLabel")
            self.latestLedLabel.setProperty("class", "infoChip")
            self.latestLedLabel.setStyleSheet("")

            self.latestEventLabel = QtWidgets.QLabel("最近事件: 等待数据", summary_widget)
            self.latestEventLabel.setObjectName("latestEventLabel")
            self.latestEventLabel.setProperty("class", "infoChip")
            self.latestEventLabel.setWordWrap(True)
            self.latestEventLabel.setStyleSheet("")

            self.showHeartbeatCheck = QtWidgets.QCheckBox("显示心跳日志", summary_widget)
            self.showHeartbeatCheck.setChecked(False)
            self.autoScrollCheck = QtWidgets.QCheckBox("日志自动滚动", summary_widget)
            self.autoScrollCheck.setChecked(True)

            summary_layout.addWidget(self.latestDisplayLabel, 0, 0)
            summary_layout.addWidget(self.latestLedLabel, 0, 1)
            summary_layout.addWidget(self.showHeartbeatCheck, 0, 2)
            summary_layout.addWidget(self.autoScrollCheck, 0, 3)
            summary_layout.addWidget(self.latestEventLabel, 1, 0, 1, 4)

            log_layout.insertWidget(0, summary_widget)

        self.ui.clearLogButton.setMinimumHeight(30)
        self.ui.exportLogButton.setMinimumHeight(30)
        self.ui.horizontalLayout_6.setSpacing(10)
        self.ui.horizontalLayout_6.setStretch(0, 1)
        self.ui.horizontalLayout_6.setStretch(1, 1)

        for button in self.findChildren(QtWidgets.QPushButton):
            button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        for combo in self.findChildren(QtWidgets.QComboBox):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)

        self.ui.messageEdit.setClearButtonEnabled(True)
        self.ui.rawCommandEdit.setClearButtonEnabled(True)
        self.ui.ledHexEdit.setClearButtonEnabled(True)
        for spinbox in (self.ui.dateEdit, self.ui.timeEdit, self.ui.alarmTimeEdit):
            line_edit = spinbox.findChild(QtWidgets.QLineEdit)
            if line_edit is not None:
                line_edit.setClearButtonEnabled(False)

        self.ui.gridLayout.setColumnStretch(0, 1)
        self.ui.gridLayout.setColumnStretch(1, 4)
        self.ui.gridLayout.setColumnStretch(2, 2)
        self.ui.gridLayout.setColumnStretch(3, 2)
        self.ui.gridLayout.setHorizontalSpacing(12)
        self.ui.gridLayout.setVerticalSpacing(10)
        self.ui.gridLayout.setContentsMargins(14, 10, 14, 10)
        self.ui.gridLayout_2.setColumnStretch(0, 1)
        self.ui.gridLayout_2.setColumnStretch(1, 4)
        self.ui.gridLayout_2.setColumnStretch(2, 2)
        self.ui.gridLayout_2.setHorizontalSpacing(12)
        self.ui.gridLayout_2.setVerticalSpacing(10)
        self.ui.gridLayout_2.setContentsMargins(14, 10, 14, 10)
        self.ui.verticalLayout_2.setSpacing(10)
        self.ui.verticalLayout_2.setContentsMargins(14, 10, 14, 12)
        self.ui.verticalLayout_3.setSpacing(8)
        self.ui.verticalLayout_3.setContentsMargins(14, 10, 14, 12)
        self.ui.verticalLayout_4.setContentsMargins(14, 6, 14, 10)
        self.ui.verticalLayout_5.setContentsMargins(14, 10, 14, 12)

        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送预设")
        self.ui.mixedCaseDemoButton.setText("混合大小写")
        self.ui.portHintLabel.setText("115200 8N1，自动扫描 COM，显示延迟和事件。")

    def _refine_layout(self) -> None:
        self.resize(1500, 900)
        self.setMinimumSize(1280, 760)
        self.ui.horizontalLayout.setContentsMargins(14, 14, 14, 14)
        self.ui.horizontalLayout.setSpacing(14)

        while self.ui.horizontalLayout.count():
            item = self.ui.horizontalLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for group in (
            self.ui.connectionGroup,
            self.ui.clockGroup,
            self.ui.displayGroup,
            self.ui.demoGroup,
            self.ui.twinGroup,
            self.ui.logGroup,
        ):
            group.setParent(None)

        def make_tab_page(*groups: QtWidgets.QGroupBox) -> QtWidgets.QScrollArea:
            page = QtWidgets.QScrollArea(self.ui.centralwidget)
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            page.setFrameShape(QtWidgets.QFrame.NoFrame)

            host = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(host)
            layout.setContentsMargins(6, 10, 6, 10)
            layout.setSpacing(14)
            for group in groups:
                group.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Preferred,
                )
                layout.addWidget(group)
            layout.addStretch(1)
            page.setWidget(host)
            return page

        left_panel = QtWidgets.QWidget(self.ui.centralwidget)
        left_panel.setFixedWidth(528)
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Expanding,
        )
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.leftTabs = QtWidgets.QTabWidget(left_panel)
        self.leftTabs.setDocumentMode(True)
        self.leftTabs.addTab(
            make_tab_page(self.ui.connectionGroup, self.ui.clockGroup), "基础控制"
        )
        self.leftTabs.addTab(make_tab_page(self.ui.displayGroup), "显示与外设")
        self.leftTabs.addTab(make_tab_page(self.ui.demoGroup), "协议调试")
        left_layout.addWidget(self.leftTabs)

        right_panel = QtWidgets.QWidget(self.ui.centralwidget)
        right_panel.setMinimumWidth(0)
        right_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(18)
        right_layout.addWidget(self.ui.twinGroup)
        right_layout.addWidget(self.ui.logGroup, 1)

        self.ui.connectionGroup.setMinimumHeight(152)
        self.ui.clockGroup.setMinimumHeight(258)
        self.ui.displayGroup.setMinimumHeight(368)
        self.ui.demoGroup.setMinimumHeight(238)

        screen = QtWidgets.QApplication.primaryScreen()
        available_height = (
            screen.availableGeometry().height() if screen is not None else 900
        )
        required_twin_height = max(
            self.twin.sizeHint().height() + 72,
            int(available_height * 0.31),
        )
        self.ui.twinGroup.setTitle("")
        self.ui.twinGroup.setFixedHeight(required_twin_height)
        self.ui.twinGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        log_height = max(188, int(available_height * 0.17))
        self.ui.logGroup.setMinimumHeight(log_height)
        self.ui.logGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )

        self.ui.horizontalLayout.addWidget(left_panel)
        self.ui.horizontalLayout.addWidget(right_panel, 1)

        log_layout = self.ui.logGroup.layout()
        if log_layout is not None and not hasattr(self, "latestDisplayLabel"):
            summary_widget = QtWidgets.QWidget(self.ui.logGroup)
            summary_layout = QtWidgets.QGridLayout(summary_widget)
            summary_layout.setContentsMargins(0, 0, 0, 0)
            summary_layout.setHorizontalSpacing(8)
            summary_layout.setVerticalSpacing(6)
            summary_layout.setColumnStretch(0, 3)
            summary_layout.setColumnStretch(1, 3)
            summary_layout.setColumnStretch(2, 0)
            summary_layout.setColumnStretch(3, 0)

            self.latestDisplayLabel = QtWidgets.QLabel("最新显示: --", summary_widget)
            self.latestDisplayLabel.setObjectName("latestDisplayLabel")
            self.latestDisplayLabel.setProperty("class", "infoChip")
            self.latestDisplayLabel.setStyleSheet("")

            self.latestLedLabel = QtWidgets.QLabel("最新 LED: --", summary_widget)
            self.latestLedLabel.setObjectName("latestLedLabel")
            self.latestLedLabel.setProperty("class", "infoChip")
            self.latestLedLabel.setStyleSheet("")

            self.latestEventLabel = QtWidgets.QLabel("最近事件: 等待数据", summary_widget)
            self.latestEventLabel.setObjectName("latestEventLabel")
            self.latestEventLabel.setProperty("class", "infoChip")
            self.latestEventLabel.setWordWrap(True)
            self.latestEventLabel.setStyleSheet("")

            self.showHeartbeatCheck = QtWidgets.QCheckBox("心跳日志", summary_widget)
            self.showHeartbeatCheck.setChecked(False)
            self.autoScrollCheck = QtWidgets.QCheckBox("自动滚动", summary_widget)
            self.autoScrollCheck.setChecked(True)

            summary_layout.addWidget(self.latestDisplayLabel, 0, 0)
            summary_layout.addWidget(self.latestLedLabel, 0, 1)
            summary_layout.addWidget(self.showHeartbeatCheck, 0, 2)
            summary_layout.addWidget(self.autoScrollCheck, 0, 3)
            summary_layout.addWidget(self.latestEventLabel, 1, 0, 1, 4)
            log_layout.insertWidget(0, summary_widget)

        self.ui.logTextEdit.setMinimumHeight(220)
        self.ui.logTextEdit.document().setMaximumBlockCount(self.max_log_blocks)
        self.ui.clearLogButton.setMinimumHeight(28)
        self.ui.exportLogButton.setMinimumHeight(28)
        self.ui.horizontalLayout_6.setSpacing(10)
        self.ui.horizontalLayout_6.setStretch(0, 1)
        self.ui.horizontalLayout_6.setStretch(1, 1)

        for button in self.findChildren(QtWidgets.QPushButton):
            button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            button.setMinimumHeight(32)

        for combo in self.findChildren(QtWidgets.QComboBox):
            combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(6)

        self.ui.messageEdit.setClearButtonEnabled(True)
        self.ui.rawCommandEdit.setClearButtonEnabled(True)
        self.ui.ledHexEdit.setClearButtonEnabled(True)
        for spinbox in (self.ui.dateEdit, self.ui.timeEdit, self.ui.alarmTimeEdit):
            line_edit = spinbox.findChild(QtWidgets.QLineEdit)
            if line_edit is not None:
                line_edit.setClearButtonEnabled(False)

        self.ui.gridLayout.setColumnStretch(0, 1)
        self.ui.gridLayout.setColumnStretch(1, 3)
        self.ui.gridLayout.setColumnStretch(2, 2)
        self.ui.gridLayout.setColumnStretch(3, 2)
        self.ui.gridLayout.setHorizontalSpacing(10)
        self.ui.gridLayout.setVerticalSpacing(10)
        self.ui.gridLayout.setContentsMargins(12, 22, 12, 12)
        self.ui.gridLayout_2.setColumnStretch(0, 1)
        self.ui.gridLayout_2.setColumnStretch(1, 3)
        self.ui.gridLayout_2.setColumnStretch(2, 2)
        self.ui.gridLayout_2.setHorizontalSpacing(10)
        self.ui.gridLayout_2.setVerticalSpacing(10)
        self.ui.gridLayout_2.setContentsMargins(12, 22, 12, 12)
        self.ui.verticalLayout_2.setSpacing(10)
        self.ui.verticalLayout_2.setContentsMargins(12, 22, 12, 12)
        self.ui.verticalLayout_3.setSpacing(8)
        self.ui.verticalLayout_3.setContentsMargins(12, 22, 12, 12)
        self.ui.verticalLayout_4.setSpacing(4)
        self.ui.verticalLayout_4.setContentsMargins(12, 2, 12, 6)
        self.ui.verticalLayout_5.setSpacing(8)
        self.ui.verticalLayout_5.setContentsMargins(12, 14, 12, 8)

        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送预设")
        self.ui.mixedCaseDemoButton.setText("混合大小写")
        self.ui.portHintLabel.setText("115200 8N1，自动扫描 COM，显示延迟和事件。")

    def _prepare_widgets(self) -> None:
        now = datetime.now()
        self.ui.dateEdit.setDate(QtCore.QDate(now.year, now.month, now.day))
        self.ui.timeEdit.setTime(QtCore.QTime(now.hour, now.minute, now.second))
        self.ui.alarmTimeEdit.setTime(QtCore.QTime(7, 30, 0))

        self.ui.displayToggleCombo.addItems(["ON", "OFF"])
        self.ui.formatCombo.addItems(["LEFT", "RIGHT"])
        self.ui.modeCombo.addItems(["DAY", "NIGHT"])
        self.ui.ledHexEdit.setText("80")
        self.ui.beepSpinBox.setValue(500)

        preset_items = [
            "*SET:DATE YEAR 2026 MONTH 06 DATE 02",
            "*SET:TIME HOUR 12 MINUTE 30 SECOND 45",
            "*SET:ALARM HOUR 07 MINUTE 30 SECOND 00",
            "*SET:MSG Hello Clock",
            "*SET:DISPLAY OFF",
            "*SET:DISPLAY ON",
        ]
        self.ui.presetCombo.addItems(preset_items)

    def _wire_signals(self) -> None:
        self.ui.refreshPortsButton.clicked.connect(self.refresh_ports)
        self.ui.connectButton.clicked.connect(self.connect_port)
        self.ui.disconnectButton.clicked.connect(self.disconnect_port)
        self.ui.applyDateButton.clicked.connect(self.apply_date)
        self.ui.queryDateButton.clicked.connect(lambda: self.send_command("*GET:DATE", "DATE"))
        self.ui.applyTimeButton.clicked.connect(self.apply_time)
        self.ui.queryTimeButton.clicked.connect(lambda: self.send_command("*GET:TIME", "TIME"))
        self.ui.applyAlarmButton.clicked.connect(self.apply_alarm)
        self.ui.disableAlarmButton.clicked.connect(
            lambda: self.send_command("*SET:ALARM OFF")
        )
        self.ui.queryAlarmButton.clicked.connect(
            lambda: self.send_command("*GET:ALARM", "ALARM")
        )
        self.ui.syncNowButton.clicked.connect(self.sync_host_time)
        self.ui.applyDisplayButton.clicked.connect(self.apply_display_state)
        self.ui.applyFormatButton.clicked.connect(self.apply_format)
        self.ui.applyModeButton.clicked.connect(self.apply_mode)
        self.ui.sendBeepButton.clicked.connect(self.send_beep)
        self.ui.sendLedButton.clicked.connect(self.send_led_overlay)
        self.ui.sendMessageButton.clicked.connect(self.send_message)
        self.ui.sendPresetButton.clicked.connect(
            lambda: self.send_command(self.ui.presetCombo.currentText())
        )
        self.ui.abbrevDemoButton.clicked.connect(
            lambda: self.send_command("*SET:DISP OFF")
        )
        self.ui.mixedCaseDemoButton.clicked.connect(
            lambda: self.send_command("*SeT:FoRmAt RiGhT")
        )
        self.ui.sendRawCommandButton.clicked.connect(self.send_raw_command)
        self.ui.clearLogButton.clicked.connect(self.ui.logTextEdit.clear)
        self.ui.exportLogButton.clicked.connect(self.export_log)
        self.twin.virtual_key_requested.connect(self.send_virtual_key)

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        current = self.ui.portCombo.currentText()
        self.ui.portCombo.blockSignals(True)
        self.ui.portCombo.clear()
        self.ui.portCombo.addItems(ports)
        if current in ports:
            self.ui.portCombo.setCurrentText(current)
        self.ui.portCombo.blockSignals(False)
        if not ports:
            self.ui.portCombo.setPlaceholderText("未发现 COM 口")

    def connect_port(self) -> None:
        port_name = self.ui.portCombo.currentText().strip()
        if not port_name:
            self.log("WARN", "没有可连接的 COM 口。")
            return

        self.disconnect_port(log_message=False)
        try:
            self.serial_port = serial.Serial(
                port=port_name,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"打开串口失败: {exc}")
            self.serial_port = None
            return

        self.status_connection.setText(f"连接: {port_name}")
        self.poll_timer.start()
        self.ping_timer.start()
        self.log("INFO", f"已连接 {port_name}")
        self.query_runtime_state()

    def disconnect_port(self, log_message: bool = True) -> None:
        self.poll_timer.stop()
        self.ping_timer.stop()
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except Exception:  # noqa: BLE001
                pass
        self.serial_port = None
        self.status_connection.setText("连接: 未连接")
        self.status_latency.setText("延迟: -- ms")
        if log_message:
            self.log("INFO", "串口已断开。")

    def query_runtime_state(self) -> None:
        self.send_command("*GET:FORMAT", "FORMAT")
        self.send_command("*GET:MODE", "MODE")
        self.send_command("*GET:ALARM", "ALARM")
        self.send_command("*GET:DISPLAY", "DISPLAY")
        self.send_ping()

    def send_ping(self) -> None:
        if not self.is_connected:
            return
        self.last_ping_monotonic = time.perf_counter()
        self.send_command("*PING")

    @property
    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command: str, expect: str | None = None) -> None:
        if not self.is_connected:
            self.log("WARN", f"未连接串口，未发送: {command}")
            return
        cleaned = command.strip()
        if not cleaned:
            return
        if expect:
            self.pending_queries.append(expect)
        try:
            self.serial_port.write((cleaned + "\r\n").encode("ascii", "ignore"))
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"发送失败: {exc}")
            self.disconnect_port(log_message=False)
            return
        if not (cleaned == "*PING" and self.showHeartbeatCheck.isChecked() is False):
            self.log("TX", cleaned)

    def poll_serial(self) -> None:
        if not self.is_connected:
            return
        try:
            waiting = self.serial_port.in_waiting
            chunk = self.serial_port.read(waiting or 1)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"串口读取失败: {exc}")
            self.disconnect_port(log_message=False)
            return

        if not chunk:
            return

        self.read_buffer += chunk.decode("ascii", errors="ignore")
        normalized = self.read_buffer.replace("\r\n", "\n").replace("\r", "\n")
        parts = normalized.split("\n")
        self.read_buffer = parts.pop() if normalized and not normalized.endswith("\n") else ""
        if normalized.endswith("\n"):
            self.read_buffer = ""
        for line in parts:
            if line:
                self.handle_line(line)

    def handle_line(self, line: str) -> None:
        parsed = parse_line(line)
        if not self._should_suppress_rx_log(parsed, line):
            self.log("RX", line)
        if parsed.kind == "event":
            self.handle_event(parsed)
        elif parsed.kind == "pong":
            if self.last_ping_monotonic is not None:
                latency_ms = (time.perf_counter() - self.last_ping_monotonic) * 1000.0
                self.status_latency.setText(f"延迟: {latency_ms:.1f} ms")
            self.last_ping_monotonic = None
            self._set_latest_event(f"PONG 延迟 {self.status_latency.text().replace('延迟: ', '')}")
        elif parsed.kind == "ok":
            self.handle_ok(parsed)
        elif parsed.kind == "error":
            self.log("ERR", parsed.data or parsed.name)

    def handle_event(self, parsed: ParsedLine) -> None:
        if parsed.name == "DISP" and parsed.extra:
            try:
                dp_mask = int(parsed.extra[0], 16)
            except ValueError:
                return
            self.twin.set_display_frame(parsed.data, dp_mask)
            self.last_display_event = (parsed.data, dp_mask)
            self.latest_display_text = f"{parsed.data} / {parsed.extra[0]}"
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
            return

        if parsed.name == "LED":
            try:
                value = int(parsed.data, 16)
            except ValueError:
                return
            self.twin.set_led_byte(value)
            self.last_led_event = value
            self.latest_led_text = parsed.data.upper()
            self.latestLedLabel.setText(f"最新 LED: {self.latest_led_text}")
            return

        if parsed.name == "MODE":
            self.last_mode = parsed.data.strip() or "DAY"
            self.status_mode.setText(f"MODE: {self.last_mode}")
            self._set_latest_event(f"模式切换 -> {self.last_mode}")
            return

        if parsed.name == "KEY":
            key = parsed.data.strip().upper()
            self._set_latest_event(f"按键事件 -> {key}")
            return

        if parsed.name == "ALARM":
            self.last_alarm = "RINGING"
            self.status_alarm.setText("ALARM: RINGING")
            self._set_latest_event("闹钟开始响铃")
            return

        if parsed.name == "ALARM_OFF":
            self.last_alarm = "OFF"
            self.status_alarm.setText("ALARM: OFF")
            self._set_latest_event("闹钟停止")
            return

        if parsed.name == "EDIT" and parsed.extra:
            self.log("INFO", f"板端保存 {parsed.data}: {parsed.extra[0]}")
            self._set_latest_event(f"保存 {parsed.data}: {parsed.extra[0]}")

    def handle_ok(self, parsed: ParsedLine) -> None:
        if not self.pending_queries:
            return
        query = self.pending_queries.popleft()
        data = parsed.data.strip()
        if query == "FORMAT" and data:
            self.status_format.setText(f"FORMAT: {data}")
            if data in {"LEFT", "RIGHT"}:
                self.ui.formatCombo.setCurrentText(data)
        elif query == "MODE" and data:
            self.status_mode.setText(f"MODE: {data}")
            self.last_mode = data
            if data in {"DAY", "NIGHT"}:
                self.ui.modeCombo.setCurrentText(data)
        elif query == "ALARM":
            self.status_alarm.setText(f"ALARM: {data or 'OFF'}")
            self.last_alarm = data or "OFF"
        elif query == "DISPLAY" and data in {"ON", "OFF"}:
            self.ui.displayToggleCombo.setCurrentText(data)

    def apply_date(self) -> None:
        date = self.ui.dateEdit.date()
        command = (
            f"*SET:DATE YEAR {date.year():04d} "
            f"MONTH {date.month():02d} DATE {date.day():02d}"
        )
        self.send_command(command)

    def apply_time(self) -> None:
        time_value = self.ui.timeEdit.time()
        command = (
            f"*SET:TIME HOUR {time_value.hour():02d} "
            f"MINUTE {time_value.minute():02d} SECOND {time_value.second():02d}"
        )
        self.send_command(command)

    def apply_alarm(self) -> None:
        time_value = self.ui.alarmTimeEdit.time()
        command = (
            f"*SET:ALARM HOUR {time_value.hour():02d} "
            f"MINUTE {time_value.minute():02d} SECOND {time_value.second():02d}"
        )
        self.send_command(command)

    def sync_host_time(self) -> None:
        if self.sync_in_progress:
            return

        now = datetime.now()
        self.sync_snapshot = now.replace(microsecond=0)
        self.sync_in_progress = True
        self.ui.syncNowButton.setEnabled(False)
        self.ui.dateEdit.setDate(QtCore.QDate(now.year, now.month, now.day))
        self.ui.timeEdit.setTime(QtCore.QTime(now.hour, now.minute, now.second))
        if self.sync_snapshot is not None:
            self.send_command(build_set_date_command(self.sync_snapshot))
        QtCore.QTimer.singleShot(180, self._sync_host_time_step2)

    def _sync_host_time_step2(self) -> None:
        snapshot = self.sync_snapshot
        if snapshot is not None:
            self.send_command(build_set_time_command(snapshot))
            self.log("INFO", "已一键对时并写入 S800。")
        QtCore.QTimer.singleShot(220, self._finish_sync_host_time)

    def _finish_sync_host_time(self) -> None:
        self.sync_in_progress = False
        self.sync_snapshot = None
        self.ui.syncNowButton.setEnabled(True)

    def apply_display_state(self) -> None:
        self.send_command(f"*SET:DISPLAY {self.ui.displayToggleCombo.currentText()}")

    def apply_format(self) -> None:
        value = self.ui.formatCombo.currentText()
        self.send_command(f"*SET:FORMAT {value}")
        QtCore.QTimer.singleShot(
            180, lambda: self.send_command("*GET:FORMAT", "FORMAT")
        )

    def apply_mode(self) -> None:
        value = self.ui.modeCombo.currentText()
        self.send_command(f"*SET:MODE {value}")
        QtCore.QTimer.singleShot(
            180, lambda: self.send_command("*GET:MODE", "MODE")
        )

    def send_beep(self) -> None:
        self.send_command(f"*SET:BEEP {self.ui.beepSpinBox.value()}")

    def send_led_overlay(self) -> None:
        value = self.ui.ledHexEdit.text().strip().upper()
        self.send_command(f"*SET:LED {value}")

    def send_message(self) -> None:
        text = self.ui.messageEdit.text().strip()
        if not text:
            self.log("WARN", "消息为空，未发送。")
            return
        self.send_command(f"*SET:MSG {text}")

    def send_virtual_key(self, key_name: str) -> None:
        self.send_command(f"*SET:KEY {key_name}")

    def send_raw_command(self) -> None:
        self.send_command(self.ui.rawCommandEdit.text())

    def export_log(self) -> None:
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出日志",
            str(APP_DIR / "serial_log.txt"),
            "Text Files (*.txt);;All Files (*)",
        )
        if not target:
            return
        Path(target).write_text(self.ui.logTextEdit.toPlainText(), encoding="utf-8")
        self.log("INFO", f"日志已导出到 {target}")

    def log(self, level: str, message: str) -> None:
        color_map = {
            "INFO": "#1f5b8c",
            "WARN": "#946200",
            "ERROR": "#b23a48",
            "ERR": "#b23a48",
            "TX": "#0f766e",
            "RX": "#6d28d9",
        }
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        safe = html.escape(message)
        color = color_map.get(level, "#16324f")
        self.ui.logTextEdit.append(
            f"<span style='color:{color}'><b>[{timestamp}] {level}</b></span> {safe}"
        )
        if self.autoScrollCheck.isChecked():
            self.ui.logTextEdit.moveCursor(QtGui.QTextCursor.End)

    def _should_suppress_rx_log(self, parsed: ParsedLine, raw_line: str) -> bool:
        if parsed.kind == "pong":
            return self.showHeartbeatCheck.isChecked() is False

        if parsed.kind != "event":
            return False

        if parsed.name == "DISP" and parsed.extra:
            if self.showHeartbeatCheck.isChecked() is False:
                return True
            current = (parsed.data, int(parsed.extra[0], 16))
            if self.last_display_event == current:
                return True
            return False

        if parsed.name == "LED":
            try:
                current_led = int(parsed.data, 16)
            except ValueError:
                return False
            if self.showHeartbeatCheck.isChecked() is False:
                return True
            if self.last_led_event == current_led:
                return True
            return False

        if parsed.name == "KEY" and self.showHeartbeatCheck.isChecked() is False:
            return True

        if parsed.name in {"MODE", "ALARM", "ALARM_OFF", "EDIT", "KEY"}:
            return False

        return self.showHeartbeatCheck.isChecked() is False

    def _set_latest_event(self, text: str) -> None:
        self.latest_event_text = text
        self.latestEventLabel.setText(f"最近事件: {text}")


def main() -> int:
    os.environ.pop("QT_QPA_PLATFORM", None)
    if "plugins" in QT_RUNTIME:
        QtCore.QCoreApplication.setLibraryPaths([QT_RUNTIME["plugins"]])
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
