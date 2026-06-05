from __future__ import annotations

import html
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from bootstrap_qt import configure_qt_runtime

APP_DIR = Path(__file__).resolve().parent
QT_RUNTIME = configure_qt_runtime(APP_DIR)

import serial
from PyQt5 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from extension_services import (
    build_weather_led_mask,
    build_weather_token,
    fetch_ntp_time,
    fetch_weather_snapshot,
    geocode_city,
    should_use_day_mode,
    speak_text,
    weather_code_summary,
)
from extension_store import (
    AppConfig,
    ScheduleItem,
    append_event_log,
    ensure_storage,
    load_config,
    load_recent_event_logs,
    load_schedules,
    mark_schedule_triggered,
    normalize_board_token,
    save_config,
    save_schedules,
    schedule_trigger_matches,
)
from protocol import (
    ParsedLine,
    build_set_date_command,
    build_set_ring_command,
    build_set_time_command,
    build_set_weather_command,
    parse_line,
)
from twin_widgets import DigitalTwinWidget
from ui_main import Ui_MainWindow


class MainWindow(QtWidgets.QMainWindow):
    weather_refresh_finished = QtCore.pyqtSignal(object, object, bool)

    def __init__(self) -> None:
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        ensure_storage(APP_DIR)
        self.config: AppConfig = load_config(APP_DIR)
        self.schedules: list[ScheduleItem] = load_schedules(APP_DIR)
        self.log_dir = APP_DIR / "logs"

        self.serial_port: serial.Serial | None = None
        self.read_buffer = ""
        self.pending_queries: deque[str] = deque()
        self.last_ping_monotonic: float | None = None
        self.last_mode = "DAY"
        self.last_alarm = "OFF"
        self.cached_weather_text = ""
        self.cached_weather_led_mask = 0
        self.weather_summary_text = "未刷新天气"
        self.sunrise_text = "--:--"
        self.sunset_text = "--:--"
        self.last_weather_snapshot = None
        self.last_ntp_sync_text = "未进行网络对时"
        self.last_display_event: tuple[str, int] | None = None
        self.last_led_event: int | None = None
        self.latest_display_text = "--"
        self.latest_led_text = "--"
        self.latest_event_text = "等待数据"
        self.max_log_blocks = 400
        self.sync_in_progress = False
        self.sync_snapshot: datetime | None = None
        self.pending_user1_ntp = False
        self.weather_refresh_in_progress = False
        self.last_weather_refresh_at: datetime | None = None
        self.last_mode_auto_applied = ""
        self.ring_names = [
            ("DEFAULT", "默认铃声"),
            ("WORK_START", "上课/上班开工铃"),
            ("WORK_END", "下课/下班完工铃"),
            ("WAKE", "长起床铃"),
            ("SONG", "歌声铃"),
        ]

        self.twin = DigitalTwinWidget(self)
        twin_layout = QtWidgets.QVBoxLayout(self.ui.twinContainer)
        twin_layout.setContentsMargins(0, 0, 0, 0)
        twin_layout.addWidget(self.twin)

        self._build_statusbar()
        self._apply_theme()
        self._prepare_widgets()
        self._refine_layout()
        self._wire_signals()
        self.weather_refresh_finished.connect(self._finish_weather_refresh)

        self.port_timer = QtCore.QTimer(self)
        self.port_timer.setInterval(1500)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start()

        self.extension_timer = QtCore.QTimer(self)
        self.extension_timer.setInterval(1000)
        self.extension_timer.timeout.connect(self.extension_tick)
        self.extension_timer.start()

        self.user1_sync_timer = QtCore.QTimer(self)
        self.user1_sync_timer.setSingleShot(True)
        self.user1_sync_timer.setInterval(480)
        self.user1_sync_timer.timeout.connect(self._handle_deferred_user1_ntp)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(20)
        self.poll_timer.timeout.connect(self.poll_serial)

        self.ping_timer = QtCore.QTimer(self)
        self.ping_timer.setInterval(2000)
        self.ping_timer.timeout.connect(self.send_ping)

        self.refresh_ports()
        self.sync_extension_widgets_from_config()
        self.refresh_schedule_table()
        self.refresh_dashboard()
        self._refresh_theme_from_mode()
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
        night = self.config.theme_follow_mode and self.last_mode == "NIGHT"
        palette = {
            "background": "#f4efe7",
            "text": "#16324f",
            "group_bg": "#fffdfa",
            "group_border": "#d7d0c6",
            "title": "#123b63",
            "button": "#1f5b8c",
            "button_hover": "#2b76b3",
            "button_pressed": "#174969",
            "input_bg": "#ffffff",
            "input_border": "#c5d0da",
            "chip_bg": "#eef5fb",
            "chip_border": "#c5d7e8",
            "chip_text": "#234a70",
            "tab_bg": "#e5edf5",
        }
        if night:
            palette.update(
                {
                    "background": "#18212b",
                    "text": "#dbe7f2",
                    "group_bg": "#223041",
                    "group_border": "#445365",
                    "title": "#d8ebff",
                    "button": "#255f8c",
                    "button_hover": "#3477aa",
                    "button_pressed": "#194766",
                    "input_bg": "#16202b",
                    "input_border": "#4a5a6d",
                    "chip_bg": "#233345",
                    "chip_border": "#4a5a6d",
                    "chip_text": "#dbe7f2",
                    "tab_bg": "#2b3c4d",
                }
            )
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {palette['background']};
            }}
            QWidget {{
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
                color: {palette['text']};
            }}
            QGroupBox {{
                background: {palette['group_bg']};
                border: 1px solid {palette['group_border']};
                border-radius: 10px;
                margin-top: 12px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                left: 12px;
                padding: 0 6px;
                color: {palette['title']};
            }}
            QPushButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 5px 10px;
                font-weight: 600;
                min-height: 30px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {palette['button_hover']};
            }}
            QPushButton:pressed {{
                background: {palette['button_pressed']};
            }}
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QTextEdit {{
                background: {palette['input_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                padding: 6px 8px;
            }}
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
                min-height: 32px;
            }}
            QTextEdit {{
                font-family: "Consolas";
                font-size: 11px;
            }}
            QLabel#twinTitle {{
                font-size: 10px;
                font-weight: 600;
                color: {palette['title']};
            }}
            QLabel#twinHeaderLabel {{
                font-size: 12px;
                font-weight: 700;
                color: {palette['title']};
            }}
            QLabel.infoChip {{
                background: {palette['chip_bg']};
                border: 1px solid {palette['chip_border']};
                border-radius: 8px;
                padding: 4px 8px;
                color: {palette['chip_text']};
                font-size: 11px;
            }}
            QCheckBox {{
                spacing: 6px;
                font-size: 11px;
            }}
            QToolBox {{
                background: transparent;
                border: none;
            }}
            QToolBox::tab {{
                background: {palette['tab_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                padding: 7px 12px;
                margin-bottom: 6px;
                color: {palette['title']};
                font-weight: 600;
            }}
            QToolBox::tab:selected {{
                background: {palette['group_bg']};
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            """
        )

    def _refresh_theme_from_mode(self) -> None:
        if hasattr(self, "themeModeLabel"):
            self.themeModeLabel.setText(f"主题状态: {self.last_mode}")
        self._apply_theme()

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

        self.leftSections = QtWidgets.QToolBox(left_panel)
        self.leftSections.addItem(
            make_tab_page(self.ui.connectionGroup, self.ui.clockGroup), "基础控制"
        )
        self.leftSections.addItem(make_tab_page(self.ui.displayGroup), "显示与外设")
        self.leftSections.addItem(make_tab_page(self.ui.demoGroup), "协议调试")
        self.leftSections.addItem(self._build_extension_settings_page(), "扩展设置")
        self.leftSections.addItem(self._build_schedule_dashboard_page(), "日程与看板")
        left_layout.addWidget(self.leftSections)

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

    def _build_extension_settings_page(self) -> QtWidgets.QScrollArea:
        page = QtWidgets.QScrollArea(self.ui.centralwidget)
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        page.setFrameShape(QtWidgets.QFrame.NoFrame)

        host = QtWidgets.QWidget(page)
        outer = QtWidgets.QVBoxLayout(host)
        outer.setContentsMargins(6, 10, 6, 10)
        outer.setSpacing(14)

        network_group = QtWidgets.QGroupBox("网络对时与天气", host)
        network_layout = QtWidgets.QGridLayout(network_group)
        network_layout.setContentsMargins(12, 22, 12, 12)
        network_layout.setHorizontalSpacing(10)
        network_layout.setVerticalSpacing(10)

        self.cityEdit = QtWidgets.QLineEdit(network_group)
        self.cityEdit.setPlaceholderText("城市名，例如 Shanghai")
        self.lookupCityButton = QtWidgets.QPushButton("定位城市", network_group)
        self.saveExtensionConfigButton = QtWidgets.QPushButton("保存配置", network_group)
        self.ntpSyncButton = QtWidgets.QPushButton("NTP 对时并写入", network_group)
        self.weatherRefreshButton = QtWidgets.QPushButton("刷新天气并下发", network_group)
        self.autoDayNightCheck = QtWidgets.QCheckBox("自动昼夜模式", network_group)
        self.themeFollowCheck = QtWidgets.QCheckBox("PC 主题跟随板端模式", network_group)
        self.voiceEnabledCheck = QtWidgets.QCheckBox("启用语音播报", network_group)
        self.quietNightCheck = QtWidgets.QCheckBox("夜间抑制扩展铃声", network_group)
        self.cityInfoLabel = QtWidgets.QLabel("经纬度: -- | 时区: --", network_group)
        self.cityInfoLabel.setProperty("class", "infoChip")
        self.weatherInfoLabel = QtWidgets.QLabel("天气: 未刷新", network_group)
        self.weatherInfoLabel.setProperty("class", "infoChip")
        self.sunriseSunsetLabel = QtWidgets.QLabel("日出/日落: -- / --", network_group)
        self.sunriseSunsetLabel.setProperty("class", "infoChip")

        network_layout.addWidget(QtWidgets.QLabel("城市"), 0, 0)
        network_layout.addWidget(self.cityEdit, 0, 1, 1, 2)
        network_layout.addWidget(self.lookupCityButton, 0, 3)
        network_layout.addWidget(self.saveExtensionConfigButton, 0, 4)
        network_layout.addWidget(self.ntpSyncButton, 1, 1, 1, 2)
        network_layout.addWidget(self.weatherRefreshButton, 1, 3, 1, 2)
        network_layout.addWidget(self.autoDayNightCheck, 2, 1)
        network_layout.addWidget(self.themeFollowCheck, 2, 2)
        network_layout.addWidget(self.voiceEnabledCheck, 2, 3)
        network_layout.addWidget(self.quietNightCheck, 2, 4)
        network_layout.addWidget(self.cityInfoLabel, 3, 1, 1, 4)
        network_layout.addWidget(self.weatherInfoLabel, 4, 1, 1, 4)
        network_layout.addWidget(self.sunriseSunsetLabel, 5, 1, 1, 4)

        ring_group = QtWidgets.QGroupBox("扩展提醒与铃声", host)
        ring_layout = QtWidgets.QGridLayout(ring_group)
        ring_layout.setContentsMargins(12, 22, 12, 12)
        ring_layout.setHorizontalSpacing(10)
        ring_layout.setVerticalSpacing(10)

        self.ringPreviewCombo = QtWidgets.QComboBox(ring_group)
        self.ringPreviewCombo.addItems([label for _, label in self.ring_names])
        self.ringPreviewButton = QtWidgets.QPushButton("预览铃声", ring_group)
        self.themeModeLabel = QtWidgets.QLabel("主题状态: DAY", ring_group)
        self.themeModeLabel.setProperty("class", "infoChip")
        self.themeModeLabel.setStyleSheet("")
        self.ntpStatusLabel = QtWidgets.QLabel("最近 NTP: 未进行网络对时", ring_group)
        self.ntpStatusLabel.setProperty("class", "infoChip")
        self.ntpStatusLabel.setStyleSheet("")

        ring_layout.addWidget(QtWidgets.QLabel("铃声类型"), 0, 0)
        ring_layout.addWidget(self.ringPreviewCombo, 0, 1, 1, 2)
        ring_layout.addWidget(self.ringPreviewButton, 0, 3)
        ring_layout.addWidget(self.themeModeLabel, 1, 1, 1, 3)
        ring_layout.addWidget(self.ntpStatusLabel, 2, 1, 1, 3)

        outer.addWidget(network_group)
        outer.addWidget(ring_group)
        outer.addStretch(1)
        page.setWidget(host)
        return page

    def _build_schedule_dashboard_page(self) -> QtWidgets.QScrollArea:
        page = QtWidgets.QScrollArea(self.ui.centralwidget)
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        page.setFrameShape(QtWidgets.QFrame.NoFrame)

        host = QtWidgets.QWidget(page)
        outer = QtWidgets.QVBoxLayout(host)
        outer.setContentsMargins(6, 10, 6, 10)
        outer.setSpacing(14)

        schedule_group = QtWidgets.QGroupBox("多日程提醒", host)
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)
        schedule_layout.setContentsMargins(12, 22, 12, 12)
        schedule_layout.setSpacing(10)

        self.scheduleTable = QtWidgets.QTableWidget(schedule_group)
        self.scheduleTable.setColumnCount(5)
        self.scheduleTable.setHorizontalHeaderLabels(["启用", "标题", "规则", "时间", "铃声"])
        self.scheduleTable.horizontalHeader().setStretchLastSection(True)
        self.scheduleTable.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self.scheduleTable.verticalHeader().setVisible(False)
        self.scheduleTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.scheduleTable.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.scheduleTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.scheduleTable.setMinimumHeight(180)
        schedule_layout.addWidget(self.scheduleTable)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self.scheduleTitleEdit = QtWidgets.QLineEdit(schedule_group)
        self.scheduleBoardLabelEdit = QtWidgets.QLineEdit(schedule_group)
        self.scheduleBoardLabelEdit.setPlaceholderText("板端短标签，最多 8 字符")
        self.scheduleTimeEdit = QtWidgets.QTimeEdit(schedule_group)
        self.scheduleTimeEdit.setDisplayFormat("HH:mm:ss")
        self.scheduleTimeEdit.setTime(QtCore.QTime(8, 0, 0))
        self.scheduleTypeCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleTypeCombo.addItems(["单次日期", "每周重复"])
        self.scheduleDateEdit = QtWidgets.QDateEdit(schedule_group)
        self.scheduleDateEdit.setCalendarPopup(True)
        self.scheduleDateEdit.setDate(QtCore.QDate.currentDate())
        self.scheduleRingCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleRingCombo.addItems([label for _, label in self.ring_names])
        self.scheduleVoiceEdit = QtWidgets.QLineEdit(schedule_group)
        self.scheduleVoiceEdit.setPlaceholderText("语音播报文案，留空则用标题")
        self.scheduleEnabledCheck = QtWidgets.QCheckBox("启用此提醒", schedule_group)
        self.scheduleEnabledCheck.setChecked(True)
        self.scheduleWeekdayChecks: list[QtWidgets.QCheckBox] = []

        weekday_host = QtWidgets.QWidget(schedule_group)
        weekday_layout = QtWidgets.QHBoxLayout(weekday_host)
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_layout.setSpacing(6)
        for text in ["一", "二", "三", "四", "五", "六", "日"]:
            check = QtWidgets.QCheckBox(text, weekday_host)
            weekday_layout.addWidget(check)
            self.scheduleWeekdayChecks.append(check)
        weekday_layout.addStretch(1)

        self.scheduleSaveButton = QtWidgets.QPushButton("新增 / 更新提醒", schedule_group)
        self.scheduleResetButton = QtWidgets.QPushButton("清空表单", schedule_group)
        self.scheduleDeleteButton = QtWidgets.QPushButton("删除选中提醒", schedule_group)

        form.addWidget(QtWidgets.QLabel("标题"), 0, 0)
        form.addWidget(self.scheduleTitleEdit, 0, 1)
        form.addWidget(QtWidgets.QLabel("板端标签"), 0, 2)
        form.addWidget(self.scheduleBoardLabelEdit, 0, 3)
        form.addWidget(QtWidgets.QLabel("时间"), 1, 0)
        form.addWidget(self.scheduleTimeEdit, 1, 1)
        form.addWidget(QtWidgets.QLabel("规则"), 1, 2)
        form.addWidget(self.scheduleTypeCombo, 1, 3)
        form.addWidget(QtWidgets.QLabel("日期"), 2, 0)
        form.addWidget(self.scheduleDateEdit, 2, 1)
        form.addWidget(QtWidgets.QLabel("每周"), 2, 2)
        form.addWidget(weekday_host, 2, 3)
        form.addWidget(QtWidgets.QLabel("铃声"), 3, 0)
        form.addWidget(self.scheduleRingCombo, 3, 1)
        form.addWidget(QtWidgets.QLabel("语音"), 3, 2)
        form.addWidget(self.scheduleVoiceEdit, 3, 3)
        form.addWidget(self.scheduleEnabledCheck, 4, 1)
        form.addWidget(self.scheduleSaveButton, 4, 2)
        form.addWidget(self.scheduleResetButton, 4, 3)
        form.addWidget(self.scheduleDeleteButton, 5, 2, 1, 2)
        schedule_layout.addLayout(form)

        dashboard_group = QtWidgets.QGroupBox("扩展数据看板", host)
        dashboard_layout = QtWidgets.QVBoxLayout(dashboard_group)
        dashboard_layout.setContentsMargins(12, 22, 12, 12)
        dashboard_layout.setSpacing(10)

        self.dashboardSummaryLabel = QtWidgets.QLabel("统计: --", dashboard_group)
        self.dashboardSummaryLabel.setProperty("class", "infoChip")
        self.dashboardSummaryLabel.setStyleSheet("")
        self.dashboardModeLabel = QtWidgets.QLabel("模式切换: --", dashboard_group)
        self.dashboardModeLabel.setProperty("class", "infoChip")
        self.dashboardModeLabel.setStyleSheet("")
        self.dashboardWeatherLabel = QtWidgets.QLabel("天气刷新: --", dashboard_group)
        self.dashboardWeatherLabel.setProperty("class", "infoChip")
        self.dashboardWeatherLabel.setStyleSheet("")
        self.dashboardEventList = QtWidgets.QListWidget(dashboard_group)
        self.dashboardEventList.setMinimumHeight(140)

        dashboard_layout.addWidget(self.dashboardSummaryLabel)
        dashboard_layout.addWidget(self.dashboardModeLabel)
        dashboard_layout.addWidget(self.dashboardWeatherLabel)
        dashboard_layout.addWidget(self.dashboardEventList)

        outer.addWidget(schedule_group)
        outer.addWidget(dashboard_group)
        outer.addStretch(1)
        page.setWidget(host)
        return page

    def sync_extension_widgets_from_config(self) -> None:
        if not hasattr(self, "cityEdit"):
            return
        self.cityEdit.setText(self.config.city_name)
        self.autoDayNightCheck.setChecked(self.config.auto_day_night)
        self.themeFollowCheck.setChecked(self.config.theme_follow_mode)
        self.voiceEnabledCheck.setChecked(self.config.voice_enabled)
        self.quietNightCheck.setChecked(self.config.quiet_night_rings)
        self.cityInfoLabel.setText(
            f"经纬度: {self.config.latitude:.4f}, {self.config.longitude:.4f} | 时区: {self.config.timezone}"
        )
        self.weatherInfoLabel.setText(f"天气: {self.weather_summary_text}")
        self.sunriseSunsetLabel.setText(f"日出/日落: {self.sunrise_text} / {self.sunset_text}")
        self.ntpStatusLabel.setText(f"最近 NTP: {self.last_ntp_sync_text}")
        if hasattr(self, "scheduleTypeCombo"):
            self._sync_schedule_type_ui()

    def refresh_schedule_table(self) -> None:
        if not hasattr(self, "scheduleTable"):
            return
        self.scheduleTable.setRowCount(len(self.schedules))
        for row, item in enumerate(self.schedules):
            self.scheduleTable.setItem(row, 0, QtWidgets.QTableWidgetItem("是" if item.enabled else "否"))
            self.scheduleTable.setItem(row, 1, QtWidgets.QTableWidgetItem(item.title))
            if item.schedule_type == "weekly":
                weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                rule = "每周 " + ",".join(
                    weekday_names[index] for index in item.weekdays if 0 <= index < 7
                )
            else:
                rule = item.target_date or "--"
            self.scheduleTable.setItem(row, 2, QtWidgets.QTableWidgetItem(rule))
            self.scheduleTable.setItem(row, 3, QtWidgets.QTableWidgetItem(item.trigger_time))
            self.scheduleTable.setItem(row, 4, QtWidgets.QTableWidgetItem(item.ring_type))

    def refresh_dashboard(self) -> None:
        if not hasattr(self, "dashboardSummaryLabel"):
            return
        enabled_count = len([item for item in self.schedules if item.enabled])
        self.dashboardSummaryLabel.setText(
            f"统计: 共 {len(self.schedules)} 条提醒，启用 {enabled_count} 条，最近 NTP {self.last_ntp_sync_text}"
        )
        self.dashboardModeLabel.setText(
            f"模式切换: 当前 {self.last_mode} | 自动昼夜 {'开' if self.config.auto_day_night else '关'}"
        )
        self.dashboardWeatherLabel.setText(
            f"天气刷新: {self.weather_summary_text} | 日出 {self.sunrise_text} | 日落 {self.sunset_text}"
        )
        self.dashboardEventList.clear()
        for entry in load_recent_event_logs(APP_DIR, limit=8):
            when = entry.get("when", "--")
            kind = entry.get("kind", "event")
            detail = entry.get("detail", "")
            self.dashboardEventList.addItem(f"{when} | {kind} | {detail}")

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
        self.lookupCityButton.clicked.connect(self.lookup_city)
        self.saveExtensionConfigButton.clicked.connect(self.save_extension_config)
        self.ntpSyncButton.clicked.connect(self.sync_ntp_time)
        self.weatherRefreshButton.clicked.connect(self.refresh_weather_and_push)
        self.ringPreviewButton.clicked.connect(self.preview_ring)
        self.scheduleSaveButton.clicked.connect(self.save_schedule_item)
        self.scheduleResetButton.clicked.connect(self.reset_schedule_form)
        self.scheduleDeleteButton.clicked.connect(self.delete_selected_schedule)
        self.scheduleTable.itemSelectionChanged.connect(self.load_selected_schedule)
        self.scheduleTypeCombo.currentIndexChanged.connect(self._sync_schedule_type_ui)

    def extension_tick(self) -> None:
        now = datetime.now()
        if self.pending_user1_ntp and not self.user1_sync_timer.isActive():
            self.user1_sync_timer.start()

        if self.config.auto_day_night and self.last_weather_refresh_at is not None:
            if now.second == 0:
                self.apply_auto_day_night(now)

        if self.last_weather_refresh_at is None or (
            now - self.last_weather_refresh_at
        ) >= timedelta(minutes=max(5, self.config.weather_refresh_minutes)):
            self.refresh_weather_and_push(log_trigger=False)

        schedule_changed = False
        for item in self.schedules:
            if schedule_trigger_matches(item, now):
                self.trigger_schedule(item)
                mark_schedule_triggered(item, now)
                schedule_changed = True
        if schedule_changed:
            save_schedules(APP_DIR, self.schedules)
            self.refresh_schedule_table()
            self.refresh_dashboard()

    def _handle_deferred_user1_ntp(self) -> None:
        if not self.pending_user1_ntp:
            return
        self.pending_user1_ntp = False
        self.sync_ntp_time(trigger_source="USER1")

    def lookup_city(self) -> None:
        city = self.cityEdit.text().strip()
        if not city:
            self.log("WARN", "城市名为空，未定位。")
            return
        try:
            result = geocode_city(city)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"城市定位失败: {exc}")
            return
        self.config.city_name = result.name
        self.config.latitude = result.latitude
        self.config.longitude = result.longitude
        self.config.timezone = result.timezone
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self.log("INFO", f"已定位城市: {result.name} ({result.latitude:.4f}, {result.longitude:.4f})")
        self.refresh_weather_and_push()

    def save_extension_config(self) -> None:
        self.config.city_name = self.cityEdit.text().strip() or self.config.city_name
        self.config.auto_day_night = self.autoDayNightCheck.isChecked()
        self.config.theme_follow_mode = self.themeFollowCheck.isChecked()
        self.config.voice_enabled = self.voiceEnabledCheck.isChecked()
        self.config.quiet_night_rings = self.quietNightCheck.isChecked()
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self._refresh_theme_from_mode()
        self.refresh_dashboard()
        self.log("INFO", "扩展配置已保存。")

    def _send_datetime_snapshot(self, moment: datetime, source_text: str) -> None:
        if self.sync_in_progress:
            return
        self.sync_snapshot = moment.replace(microsecond=0)
        self.sync_in_progress = True
        self.ui.syncNowButton.setEnabled(False)
        self.ntpSyncButton.setEnabled(False)
        self.ui.dateEdit.setDate(
            QtCore.QDate(self.sync_snapshot.year, self.sync_snapshot.month, self.sync_snapshot.day)
        )
        self.ui.timeEdit.setTime(
            QtCore.QTime(self.sync_snapshot.hour, self.sync_snapshot.minute, self.sync_snapshot.second)
        )
        self.send_command(build_set_date_command(self.sync_snapshot))
        QtCore.QTimer.singleShot(220, self._sync_host_time_step2)
        self.last_ntp_sync_text = source_text
        self.ntpStatusLabel.setText(f"最近 NTP: {source_text}")

    def sync_ntp_time(self, trigger_source: str = "按钮") -> None:
        try:
            snapshot = fetch_ntp_time(self.config.ntp_host)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"NTP 对时失败: {exc}")
            append_event_log(APP_DIR, "ntp_error", str(exc))
            self.refresh_dashboard()
            return
        self._send_datetime_snapshot(snapshot, snapshot.strftime("%Y-%m-%d %H:%M:%S"))
        append_event_log(APP_DIR, "ntp_sync", f"{trigger_source} -> {snapshot.isoformat(sep=' ')}")
        self.log("INFO", f"NTP 对时成功并写入 S800（来源: {trigger_source}）。")
        self.refresh_dashboard()

    def refresh_weather_and_push(self, log_trigger: bool = True) -> None:
        if self.weather_refresh_in_progress:
            return
        self.weather_refresh_in_progress = True
        city_name = self.config.city_name
        latitude = self.config.latitude
        longitude = self.config.longitude
        timezone_name = self.config.timezone

        def worker() -> None:
            snapshot = None
            error = None
            try:
                snapshot = fetch_weather_snapshot(
                    city_name,
                    latitude,
                    longitude,
                    timezone_name,
                )
            except Exception as exc:  # noqa: BLE001
                error = exc
            self.weather_refresh_finished.emit(snapshot, error, log_trigger)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_weather_refresh(self, snapshot, error, log_trigger: bool) -> None:
        self.weather_refresh_in_progress = False
        if error is not None:
            self.last_weather_refresh_at = datetime.now()
            if log_trigger:
                self.log("ERROR", f"天气刷新失败: {error}")
            append_event_log(APP_DIR, "weather_error", str(error))
            self.refresh_dashboard()
            return
        if snapshot is None:
            return

        self.last_weather_refresh_at = datetime.now()
        self.last_weather_snapshot = snapshot
        self.cached_weather_text = snapshot.display_token or build_weather_token(
            snapshot.summary, snapshot.temperature_c
        )
        self.cached_weather_led_mask = snapshot.led_mask or build_weather_led_mask(
            snapshot.weather_code, snapshot.temperature_c
        )
        self.weather_summary_text = (
            f"{weather_code_summary(snapshot.weather_code)} {snapshot.temperature_c:.1f}C"
        )
        self.sunrise_text = snapshot.sunrise_at.strftime("%H:%M")
        self.sunset_text = snapshot.sunset_at.strftime("%H:%M")
        self.sync_extension_widgets_from_config()
        self.refresh_dashboard()
        append_event_log(
            APP_DIR,
            "weather_refresh",
            f"{self.weather_summary_text} | {self.cached_weather_text} | LED {self.cached_weather_led_mask:02X}",
        )
        if self.is_connected:
            self.send_command(
                build_set_weather_command(self.cached_weather_text, self.cached_weather_led_mask)
            )
        if log_trigger:
            self.log("INFO", f"天气已刷新并下发: {self.weather_summary_text}")
        if self.config.auto_day_night:
            self.apply_auto_day_night(datetime.now())

    def apply_auto_day_night(self, now: datetime) -> None:
        if self.last_weather_snapshot is None:
            return
        expected_mode = (
            "DAY" if should_use_day_mode(now, self.last_weather_snapshot) else "NIGHT"
        )
        if expected_mode == self.last_mode_auto_applied and expected_mode == self.last_mode:
            return
        self.last_mode_auto_applied = expected_mode
        if self.is_connected:
            self.send_command(f"*SET:MODE {expected_mode}")
        self.last_mode = expected_mode
        self._refresh_theme_from_mode()
        append_event_log(APP_DIR, "auto_mode", expected_mode)
        self.refresh_dashboard()

    def preview_ring(self) -> None:
        ring_name = self.ring_names[self.ringPreviewCombo.currentIndex()][0]
        self.send_command(build_set_ring_command(ring_name))
        self.log("INFO", f"已预览铃声: {ring_name}")

    def reset_schedule_form(self) -> None:
        self.scheduleTable.clearSelection()
        self.scheduleTitleEdit.clear()
        self.scheduleBoardLabelEdit.clear()
        self.scheduleTimeEdit.setTime(QtCore.QTime(8, 0, 0))
        self.scheduleTypeCombo.setCurrentIndex(0)
        self.scheduleDateEdit.setDate(QtCore.QDate.currentDate())
        self.scheduleRingCombo.setCurrentIndex(0)
        self.scheduleVoiceEdit.clear()
        self.scheduleEnabledCheck.setChecked(True)
        for check in self.scheduleWeekdayChecks:
            check.setChecked(False)
        self._sync_schedule_type_ui()

    def _sync_schedule_type_ui(self) -> None:
        weekly = self.scheduleTypeCombo.currentIndex() == 1
        self.scheduleDateEdit.setEnabled(not weekly)
        for check in self.scheduleWeekdayChecks:
            check.setEnabled(weekly)

    def _selected_schedule_index(self) -> int | None:
        rows = self.scheduleTable.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def _collect_schedule_item(self, existing_id: str | None = None) -> ScheduleItem:
        title = self.scheduleTitleEdit.text().strip() or "未命名提醒"
        board_label = self.scheduleBoardLabelEdit.text().strip() or normalize_board_token(title)
        trigger_time = self.scheduleTimeEdit.time().toString("HH:mm:ss")
        schedule_type = "weekly" if self.scheduleTypeCombo.currentIndex() == 1 else "once"
        weekdays = []
        if schedule_type == "weekly":
            weekdays = [
                index
                for index, check in enumerate(self.scheduleWeekdayChecks)
                if check.isChecked()
            ]
        target_date = self.scheduleDateEdit.date().toString("yyyy-MM-dd")
        ring_type = self.ring_names[self.scheduleRingCombo.currentIndex()][0]
        voice_text = self.scheduleVoiceEdit.text().strip()
        return ScheduleItem(
            item_id=existing_id or f"schedule-{int(time.time() * 1000)}",
            title=title,
            board_label=board_label,
            trigger_time=trigger_time,
            schedule_type=schedule_type,
            weekdays=weekdays,
            target_date=target_date if schedule_type == "once" else None,
            ring_type=ring_type,
            enabled=self.scheduleEnabledCheck.isChecked(),
            voice_text=voice_text or None,
        )

    def save_schedule_item(self) -> None:
        index = self._selected_schedule_index()
        existing_id = self.schedules[index].item_id if index is not None else None
        item = self._collect_schedule_item(existing_id)
        if item.schedule_type == "weekly" and not item.weekdays:
            self.log("WARN", "每周重复提醒至少要勾选一天。")
            return
        if index is None:
            self.schedules.append(item)
            self.log("INFO", f"已新增提醒: {item.title}")
        else:
            self.schedules[index] = item
            self.log("INFO", f"已更新提醒: {item.title}")
        save_schedules(APP_DIR, self.schedules)
        append_event_log(APP_DIR, "schedule_save", item.title)
        self.refresh_schedule_table()
        self.refresh_dashboard()
        self.reset_schedule_form()

    def load_selected_schedule(self) -> None:
        index = self._selected_schedule_index()
        if index is None:
            return
        item = self.schedules[index]
        self.scheduleTitleEdit.setText(item.title)
        self.scheduleBoardLabelEdit.setText(item.board_label)
        self.scheduleTimeEdit.setTime(QtCore.QTime.fromString(item.trigger_time, "HH:mm:ss"))
        self.scheduleTypeCombo.setCurrentIndex(1 if item.schedule_type == "weekly" else 0)
        if item.target_date:
            self.scheduleDateEdit.setDate(QtCore.QDate.fromString(item.target_date, "yyyy-MM-dd"))
        for check in self.scheduleWeekdayChecks:
            check.setChecked(False)
        for index2, check in enumerate(self.scheduleWeekdayChecks):
            if index2 in item.weekdays:
                check.setChecked(True)
        ring_index = next(
            (idx for idx, pair in enumerate(self.ring_names) if pair[0] == item.ring_type),
            0,
        )
        self.scheduleRingCombo.setCurrentIndex(ring_index)
        self.scheduleVoiceEdit.setText(item.voice_text or "")
        self.scheduleEnabledCheck.setChecked(item.enabled)
        self._sync_schedule_type_ui()

    def delete_selected_schedule(self) -> None:
        index = self._selected_schedule_index()
        if index is None:
            self.log("WARN", "没有选中要删除的提醒。")
            return
        title = self.schedules[index].title
        del self.schedules[index]
        save_schedules(APP_DIR, self.schedules)
        append_event_log(APP_DIR, "schedule_delete", title)
        self.refresh_schedule_table()
        self.refresh_dashboard()
        self.reset_schedule_form()
        self.log("INFO", f"已删除提醒: {title}")

    def trigger_schedule(self, item: ScheduleItem) -> None:
        token = normalize_board_token(item.board_label or item.title)
        if self.is_connected:
            self.send_command(f"*SET:MSG {token}")
            if not (self.config.quiet_night_rings and self.last_mode == "NIGHT"):
                self.send_command(build_set_ring_command(item.ring_type))
        if self.config.voice_enabled:
            speak_text(item.voice_text or item.title)
        append_event_log(APP_DIR, "schedule_fire", f"{item.title} | {item.ring_type}")
        self.log("INFO", f"提醒触发: {item.title}")
        self.refresh_dashboard()

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
        if self.cached_weather_text:
            self.send_command(
                build_set_weather_command(self.cached_weather_text, self.cached_weather_led_mask)
            )

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
            append_event_log(APP_DIR, "error", parsed.data or parsed.name)
            self.refresh_dashboard()

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
            self.pending_user1_ntp = False
            self.user1_sync_timer.stop()
            self._refresh_theme_from_mode()
            append_event_log(APP_DIR, "mode", self.last_mode)
            self.refresh_dashboard()
            self._set_latest_event(f"模式切换 -> {self.last_mode}")
            return

        if parsed.name == "KEY":
            key = parsed.data.strip().upper()
            if key == "USER1":
                self.pending_user1_ntp = True
                self.user1_sync_timer.start()
            append_event_log(APP_DIR, "key", key)
            self.refresh_dashboard()
            self._set_latest_event(f"按键事件 -> {key}")
            return

        if parsed.name == "ALARM":
            self.last_alarm = "RINGING"
            self.status_alarm.setText("ALARM: RINGING")
            append_event_log(APP_DIR, "alarm", "RINGING")
            if self.config.voice_enabled:
                speak_text("基础闹钟已触发")
            self.refresh_dashboard()
            self._set_latest_event("闹钟开始响铃")
            return

        if parsed.name == "ALARM_OFF":
            self.last_alarm = "OFF"
            self.status_alarm.setText("ALARM: OFF")
            append_event_log(APP_DIR, "alarm", "OFF")
            self.refresh_dashboard()
            self._set_latest_event("闹钟停止")
            return

        if parsed.name == "EDIT" and parsed.extra:
            self.log("INFO", f"板端保存 {parsed.data}: {parsed.extra[0]}")
            append_event_log(APP_DIR, "edit", f"{parsed.data}: {parsed.extra[0]}")
            self.refresh_dashboard()
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
                self._refresh_theme_from_mode()
                self.refresh_dashboard()
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
        self.sync_ntp_time(trigger_source="按钮")

    def _sync_host_time_step2(self) -> None:
        snapshot = self.sync_snapshot
        if snapshot is not None:
            self.send_command(build_set_time_command(snapshot))
            self.log("INFO", "已完成对时并写入 S800。")
        QtCore.QTimer.singleShot(220, self._finish_sync_host_time)

    def _finish_sync_host_time(self) -> None:
        self.sync_in_progress = False
        self.sync_snapshot = None
        self.ui.syncNowButton.setEnabled(True)
        self.ntpSyncButton.setEnabled(True)

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
        if key_name == "USER1":
            self.sync_ntp_time(trigger_source="虚拟 USER1")
            return
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
