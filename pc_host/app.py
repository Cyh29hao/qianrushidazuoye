from __future__ import annotations

import html
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from bootstrap_qt import configure_qt_runtime

APP_DIR = Path(__file__).resolve().parent
QT_RUNTIME = configure_qt_runtime(APP_DIR)
APP_VERSION = "v2.0"
GITHUB_URL = "https://github.com/Cyh29hao"
LOGO_PATH = APP_DIR / "assets" / "clock_logo.svg"

import serial
from PyQt5 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from extension_services import (
    build_weather_led_mask,
    build_weather_token,
    format_weather_summary,
    fetch_ntp_time,
    fetch_weather_snapshot,
    geocode_city,
    should_use_day_mode,
    speak_text,
    timezone_now,
    weather_emoji,
    weather_code_summary,
)
from extension_store import (
    AppConfig,
    SavedPlace,
    ScheduleItem,
    append_event_log,
    ensure_storage,
    load_config,
    load_recent_event_logs,
    load_schedules,
    mark_schedule_triggered,
    normalize_board_token,
    parse_clock_hms,
    save_config,
    save_schedules,
    schedule_trigger_matches,
    weekday_text,
)
from protocol import (
    ParsedLine,
    build_set_date_command,
    build_set_ring_command,
    build_set_time_command,
    build_set_weather_command,
    parse_line,
)
from run_extension_checks import execute_checks_on_open_port, execute_checks_on_port
from twin_widgets import DigitalTwinWidget
from ui_main import Ui_MainWindow


class CollapsibleNavWidget(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: list[tuple[str, QtWidgets.QWidget]] = []
        self.current_index = 0
        self.menu_open = False

        self.selector_button = QtWidgets.QPushButton(self)
        self.selector_button.setObjectName("accordionHeader")
        self.selector_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.selector_button.clicked.connect(self.toggle_menu)

        self.menu_widget = QtWidgets.QWidget(self)
        self.menu_layout = QtWidgets.QVBoxLayout(self.menu_widget)
        self.menu_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_layout.setSpacing(8)
        self.menu_widget.setVisible(False)

        self.stack = QtWidgets.QStackedWidget(self)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(10)
        self.main_layout.addWidget(self.selector_button)
        self.main_layout.addWidget(self.menu_widget)
        self.main_layout.addWidget(self.stack, 1)

    def add_section(self, title: str, content: QtWidgets.QWidget) -> None:
        index = len(self.items)
        self.items.append((title, content))
        self.stack.addWidget(content)

        option_button = QtWidgets.QPushButton(f"• {title}", self.menu_widget)
        option_button.setObjectName("accordionOptionButton")
        option_button.setCursor(QtCore.Qt.PointingHandCursor)
        option_button.clicked.connect(lambda _checked=False, idx=index: self.select_index(idx))
        self.menu_layout.addWidget(option_button)

        if index == 0:
            self.select_index(0)

    def toggle_menu(self) -> None:
        self.menu_open = not self.menu_open
        self.menu_widget.setVisible(self.menu_open)
        self._refresh_selector_text()

    def select_index(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        self.current_index = index
        self.stack.setCurrentIndex(index)
        self.menu_open = False
        self.menu_widget.setVisible(False)
        self._refresh_selector_text()

    def _refresh_selector_text(self) -> None:
        if not self.items:
            self.selector_button.setText("▶ 菜单")
            return
        title = self.items[self.current_index][0]
        self.selector_button.setText(f"{'▼' if self.menu_open else '▶'} {title}")


class MainWindow(QtWidgets.QMainWindow):
    weather_refresh_finished = QtCore.pyqtSignal(object, object, bool)
    test_run_finished = QtCore.pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        ensure_storage(APP_DIR)
        self.config: AppConfig = load_config(APP_DIR)
        self.schedules: list[ScheduleItem] = load_schedules(APP_DIR)
        self.log_dir = APP_DIR / "logs"
        self.setWindowTitle(f"智能联网时钟系统 - PC 上位机 {APP_VERSION}")
        if LOGO_PATH.exists():
            self.setWindowIcon(QtGui.QIcon(str(LOGO_PATH)))

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
        self.last_selected_zone_time = "--:--:--"
        self.last_display_event: tuple[str, int] | None = None
        self.last_led_event: int | None = None
        self.latest_display_text = "--"
        self.latest_led_text = "--"
        self.latest_event_text = "等待数据"
        self.max_log_blocks = 400
        self.sync_in_progress = False
        self.sync_snapshot: datetime | None = None
        self.weather_refresh_in_progress = False
        self.last_weather_refresh_at: datetime | None = None
        self.last_mode_auto_applied = ""
        self.last_tx_command = ""
        self.last_tx_monotonic = 0.0
        self.ring_command_supported: bool | None = None
        self.last_test_summary = "未运行"
        self.last_test_ok = False
        self.test_run_in_progress = False
        self.pending_auto_test_after_apply = False
        self.last_apply_monotonic = 0.0
        self.last_ready_sync_monotonic = 0.0
        self.last_mode_expected = ""
        self.pending_mode_origin = ""
        self.pending_mode_value = ""
        self.pending_mode_deadline = 0.0
        self.board_ready_seen = False
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
        self.test_run_finished.connect(self._finish_test_run)

        self.port_timer = QtCore.QTimer(self)
        self.port_timer.setInterval(1500)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start()

        self.extension_timer = QtCore.QTimer(self)
        self.extension_timer.setInterval(1000)
        self.extension_timer.timeout.connect(self.extension_tick)
        self.extension_timer.start()

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
        self.refresh_place_combo_labels()
        self._refresh_theme_from_mode()
        self.log("INFO", "PC 上位机已启动，等待连接 S800。")

    def _build_statusbar(self) -> None:
        self.status_connection = QtWidgets.QLabel("连接: 未连接")
        self.status_format = QtWidgets.QLabel("FORMAT: LEFT")
        self.status_mode = QtWidgets.QLabel("MODE: DAY")
        self.status_alarm = QtWidgets.QLabel("ALARM: OFF")
        self.status_latency = QtWidgets.QLabel("延迟: -- ms")
        self.status_version = QtWidgets.QLabel(APP_VERSION)
        self.status_developer = QtWidgets.QLabel("开发者: Cyh29hao")
        self.status_github_button = QtWidgets.QToolButton(self)
        self.status_github_button.setText("GitHub")
        self.status_github_button.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_URL))
        )
        self.ui.statusbar.addPermanentWidget(self.status_connection)
        self.ui.statusbar.addPermanentWidget(self.status_format)
        self.ui.statusbar.addPermanentWidget(self.status_mode)
        self.ui.statusbar.addPermanentWidget(self.status_alarm)
        self.ui.statusbar.addPermanentWidget(self.status_latency)
        self.ui.statusbar.addPermanentWidget(self.status_version)
        self.ui.statusbar.addPermanentWidget(self.status_developer)
        self.ui.statusbar.addPermanentWidget(self.status_github_button)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        locked_boxes = {
            getattr(self.ui, "displayToggleCombo", None),
            getattr(self.ui, "formatCombo", None),
            getattr(self.ui, "modeCombo", None),
        }
        if watched in locked_boxes and event.type() in {
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QEvent.Wheel,
            QtCore.QEvent.KeyPress,
            QtCore.QEvent.KeyRelease,
        }:
            return True
        return super().eventFilter(watched, event)

    def _active_place(self) -> SavedPlace:
        return self.config.saved_places[self.config.active_place_index]

    def _selected_zone_now(self, utc_moment: datetime | None = None) -> datetime:
        return timezone_now(self._active_place().timezone, utc_moment)

    def _current_place_label(self, place: SavedPlace) -> str:
        current_text = timezone_now(place.timezone).strftime("%H:%M")
        return f"{place.name} {current_text}"

    def refresh_place_combo_labels(self) -> None:
        if not hasattr(self, "placeSlotCombo"):
            return
        self.placeSlotCombo.blockSignals(True)
        self.placeSlotCombo.clear()
        for place in self.config.saved_places:
            self.placeSlotCombo.addItem(self._current_place_label(place))
        self.placeSlotCombo.setCurrentIndex(self.config.active_place_index)
        self.placeSlotCombo.blockSignals(False)
        self.last_selected_zone_time = self._selected_zone_now().strftime("%Y-%m-%d %H:%M:%S")

    def _save_current_place_to_config(self) -> None:
        place = self._active_place()
        place.name = self.cityEdit.text().strip() or place.name
        self.config.saved_places[self.config.active_place_index] = place
        save_config(APP_DIR, self.config)
        self.refresh_place_combo_labels()

    def _note_auto_action(self, message: str) -> None:
        if hasattr(self, "autoModeNoticeLabel"):
            self.autoModeNoticeLabel.setText(message)
            self.autoModeNoticeLabel.setVisible(True)
        self.log("INFO", message)

    def _format_countdown(self, target: datetime | None, now: datetime) -> str:
        if target is None:
            return "无"
        delta_seconds = int((target - now).total_seconds())
        if delta_seconds <= 0:
            return "<1 分钟"
        if delta_seconds < 60:
            return "<1 分钟"
        minutes = delta_seconds // 60
        return f"{minutes} 分钟"

    def _next_single_alarm_time(self, now: datetime) -> datetime | None:
        if self.last_alarm in {"OFF", "RINGING", ""}:
            return None
        hour, minute, second = parse_clock_hms(self.last_alarm.replace(".", ":"))
        candidate = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _next_schedule_time(self, item: ScheduleItem, now: datetime) -> datetime | None:
        hour, minute, second = parse_clock_hms(item.trigger_time)
        if item.schedule_type == "once":
            if not item.target_date:
                return None
            try:
                base = datetime.strptime(item.target_date, "%Y-%m-%d")
            except ValueError:
                return None
            candidate = now.replace(
                year=base.year,
                month=base.month,
                day=base.day,
                hour=hour,
                minute=minute,
                second=second,
                microsecond=0,
            )
            return candidate if candidate >= now else None
        for offset in range(8):
            candidate = (now + timedelta(days=offset)).replace(
                hour=hour,
                minute=minute,
                second=second,
                microsecond=0,
            )
            if candidate.weekday() not in item.weekdays:
                continue
            if candidate < now:
                continue
            return candidate
        return None

    def _next_reminder_summary(self, now: datetime) -> tuple[str, datetime | None]:
        candidates: list[tuple[datetime, str]] = []
        single_alarm_at = self._next_single_alarm_time(now)
        if single_alarm_at is not None:
            candidates.append((single_alarm_at, f"单次闹钟 {single_alarm_at.strftime('%H:%M:%S')}"))
        for item in self.schedules:
            if not item.enabled:
                continue
            when = self._next_schedule_time(item, now)
            if when is not None:
                candidates.append((when, f"日程提醒 {item.title}"))
        if not candidates:
            return "无", None
        when, label = min(candidates, key=lambda pair: pair[0])
        return label, when

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
            QComboBox[stateField="true"] {{
                padding-right: 8px;
            }}
            QComboBox[stateField="true"]::drop-down {{
                width: 0px;
                border: none;
            }}
            QComboBox[stateField="true"]::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
            }}
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
                min-height: 32px;
            }}
            QTextEdit {{
                font-family: "Consolas";
                font-size: 11px;
            }}
            QListWidget, QTableWidget, QTreeWidget, QListView, QTableView, QAbstractItemView {{
                background: {palette['input_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                gridline-color: {palette['input_border']};
                selection-background-color: {palette['button']};
                selection-color: white;
            }}
            QAbstractScrollArea {{
                background: {palette['input_bg']};
            }}
            QTableWidget QWidget, QTableView QWidget {{
                background: {palette['input_bg']};
                color: {palette['text']};
            }}
            QTableWidget::item, QListWidget::item {{
                background: {palette['input_bg']};
                color: {palette['text']};
            }}
            QHeaderView::section {{
                background: {palette['chip_bg']};
                border: 1px solid {palette['input_border']};
                padding: 6px 8px;
                color: {palette['chip_text']};
                font-weight: 600;
            }}
            QTableCornerButton::section {{
                background: {palette['chip_bg']};
                border: 1px solid {palette['input_border']};
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
            QPushButton#accordionHeader {{
                background: {palette['tab_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                padding: 7px 14px;
                min-height: 38px;
                color: {palette['title']};
                font-weight: 600;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton#accordionHeader:checked {{
                background: {palette['group_bg']};
            }}
            QPushButton#accordionOptionButton {{
                background: {palette['tab_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                padding: 7px 14px;
                min-height: 36px;
                color: {palette['title']};
                font-weight: 600;
                font-size: 12px;
                text-align: left;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QWidget#sectionPageHost {{
                background: {palette['background']};
            }}
            QWidget#sectionScrollViewport {{
                background: {palette['background']};
            }}
            QStatusBar {{
                color: {palette['text']};
            }}
            QToolButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 4px 10px;
                min-height: 24px;
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )
        self._apply_dynamic_theme_overrides(night, palette)

    def _apply_dynamic_theme_overrides(self, night: bool, palette: dict[str, str]) -> None:
        list_style = (
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
        )
        header_style = (
            f"background: {palette['chip_bg']};"
            f"color: {palette['chip_text']};"
            f"border: 1px solid {palette['input_border']};"
        )
        page_style = f"background: {palette['background']}; color: {palette['text']};"
        viewport_style = f"background: {palette['input_bg']}; color: {palette['text']};"

        for widget in self.findChildren(QtWidgets.QWidget, "sectionPageHost"):
            widget.setStyleSheet(page_style)
        for area in self.findChildren(QtWidgets.QScrollArea):
            area.viewport().setStyleSheet(page_style)
        for widget in (
            getattr(self, "scheduleTable", None),
            getattr(self, "dashboardEventList", None),
            getattr(self, "testOutputText", None),
            getattr(self.ui, "logTextEdit", None),
        ):
            if widget is None:
                continue
            widget.setStyleSheet(list_style)
            if hasattr(widget, "viewport") and widget.viewport() is not None:
                widget.viewport().setStyleSheet(viewport_style)
        if hasattr(self, "scheduleTable"):
            header = self.scheduleTable.horizontalHeader()
            if header is not None:
                header.setStyleSheet(f"QHeaderView::section {{{header_style}}}")

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

        self.ui.connectButton.setText("连接并应用")
        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("切换并应用")
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

        self.leftSections = CollapsibleNavWidget(left_panel)
        self.leftSections.add_section("主页", self._build_home_page())
        self.leftSections.add_section("闹钟与日程管理", self._build_alarm_schedule_page())
        self.leftSections.add_section("系统设置", self._build_extension_settings_page())
        self.leftSections.add_section("调试与测试", self._build_debug_test_page())
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
        right_layout.addWidget(self.ui.twinGroup, 0, QtCore.Qt.AlignTop)
        right_layout.addWidget(self.ui.logGroup, 1)

        self.ui.connectionGroup.setMinimumHeight(152)
        self.ui.clockGroup.setMinimumHeight(214)
        self.ui.displayGroup.setMinimumHeight(368)
        self.ui.demoGroup.setMinimumHeight(238)

        screen = QtWidgets.QApplication.primaryScreen()
        available_height = (
            screen.availableGeometry().height() if screen is not None else 900
        )
        required_twin_height = max(
            self.twin.sizeHint().height() + 60,
            int(available_height * 0.29),
        )
        self.ui.twinGroup.setTitle("")
        self.ui.twinGroup.setFixedHeight(required_twin_height)
        self.ui.twinGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        log_height = max(220, int(available_height * 0.23))
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
            self.autoModeNoticeLabel = QtWidgets.QLabel("", summary_widget)
            self.autoModeNoticeLabel.setProperty("class", "infoChip")
            self.autoModeNoticeLabel.setStyleSheet("")
            self.autoModeNoticeLabel.setWordWrap(True)
            self.autoModeNoticeLabel.setVisible(False)

            summary_layout.addWidget(self.latestDisplayLabel, 0, 0)
            summary_layout.addWidget(self.latestLedLabel, 0, 1)
            summary_layout.addWidget(self.showHeartbeatCheck, 0, 2)
            summary_layout.addWidget(self.autoScrollCheck, 0, 3)
            summary_layout.addWidget(self.latestEventLabel, 1, 0, 1, 4)
            summary_layout.addWidget(self.autoModeNoticeLabel, 2, 0, 1, 4)
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

        self.ui.connectButton.setText("连接并应用")
        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("切换并应用")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送预设")
        self.ui.mixedCaseDemoButton.setText("混合大小写")
        self.ui.portHintLabel.setText("115200 8N1，自动扫描 COM，显示延迟和事件。")

    def _create_scroll_page(
        self,
    ) -> tuple[QtWidgets.QScrollArea, QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        page = QtWidgets.QScrollArea(self.ui.centralwidget)
        page.setObjectName("sectionScrollPage")
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        page.setFrameShape(QtWidgets.QFrame.NoFrame)
        page.viewport().setObjectName("sectionScrollViewport")
        host = QtWidgets.QWidget(page)
        host.setObjectName("sectionPageHost")
        outer = QtWidgets.QVBoxLayout(host)
        outer.setContentsMargins(6, 10, 6, 10)
        outer.setSpacing(14)
        page.setWidget(host)
        return page, host, outer

    def _configure_sync_clock_group(self) -> None:
        self.ui.clockGroup.setTitle("时间与同步")
        for widget in (
            self.ui.alarmLabel,
            self.ui.alarmTimeEdit,
            self.ui.applyAlarmButton,
            self.ui.disableAlarmButton,
            self.ui.queryAlarmButton,
        ):
            widget.setVisible(False)

    def _build_home_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()
        outer.addWidget(self.ui.connectionGroup)
        outer.addWidget(self._build_dashboard_group(host))
        outer.addStretch(1)
        return page

    def _build_board_alarm_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("板载单次闹钟", parent)
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(12, 22, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        layout.setColumnStretch(1, 1)

        self.scheduleAlarmTimeEdit = QtWidgets.QTimeEdit(group)
        self.scheduleAlarmTimeEdit.setDisplayFormat("HH:mm:ss")
        self.scheduleAlarmTimeEdit.setTime(self.ui.alarmTimeEdit.time())
        self.scheduleApplyAlarmButton = QtWidgets.QPushButton("启用/写入", group)
        self.scheduleDisableAlarmButton = QtWidgets.QPushButton("关闭", group)
        self.scheduleQueryAlarmButton = QtWidgets.QPushButton("查询闹钟", group)
        self.scheduleAlarmHintLabel = QtWidgets.QLabel(
            "离线场景可用板载单次闹钟；复杂提醒建议使用下方日程管理。",
            group,
        )
        self.scheduleAlarmHintLabel.setProperty("class", "infoChip")
        self.scheduleAlarmHintLabel.setWordWrap(True)
        self.scheduleAlarmHintLabel.setStyleSheet("")

        layout.addWidget(QtWidgets.QLabel("触发时间"), 0, 0)
        layout.addWidget(self.scheduleAlarmTimeEdit, 0, 1)
        layout.addWidget(self.scheduleApplyAlarmButton, 0, 2)
        layout.addWidget(self.scheduleQueryAlarmButton, 1, 1)
        layout.addWidget(self.scheduleDisableAlarmButton, 1, 2)
        layout.addWidget(self.scheduleAlarmHintLabel, 2, 1, 1, 2)
        return group

    def _build_schedule_management_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        schedule_group = QtWidgets.QGroupBox("多日程提醒", parent)
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)
        schedule_layout.setContentsMargins(12, 22, 12, 12)
        schedule_layout.setSpacing(10)

        self.scheduleTable = QtWidgets.QTableWidget(schedule_group)
        self.scheduleTable.setColumnCount(5)
        self.scheduleTable.setHorizontalHeaderLabels(["启用", "标题", "规则", "时间", "铃声"])
        header = self.scheduleTable.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.scheduleTable.verticalHeader().setVisible(False)
        self.scheduleTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.scheduleTable.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.scheduleTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.scheduleTable.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scheduleTable.setMinimumHeight(180)
        schedule_layout.addWidget(self.scheduleTable)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

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
        form.addWidget(QtWidgets.QLabel("板端标签"), 1, 0)
        form.addWidget(self.scheduleBoardLabelEdit, 1, 1)
        form.addWidget(QtWidgets.QLabel("时间"), 2, 0)
        form.addWidget(self.scheduleTimeEdit, 2, 1)
        form.addWidget(QtWidgets.QLabel("规则"), 3, 0)
        form.addWidget(self.scheduleTypeCombo, 3, 1)
        form.addWidget(QtWidgets.QLabel("日期"), 4, 0)
        form.addWidget(self.scheduleDateEdit, 4, 1)
        form.addWidget(QtWidgets.QLabel("每周"), 5, 0)
        form.addWidget(weekday_host, 5, 1)
        form.addWidget(QtWidgets.QLabel("铃声"), 6, 0)
        form.addWidget(self.scheduleRingCombo, 6, 1)
        form.addWidget(QtWidgets.QLabel("语音"), 7, 0)
        form.addWidget(self.scheduleVoiceEdit, 7, 1)
        form.addWidget(self.scheduleEnabledCheck, 8, 1)

        button_row = QtWidgets.QWidget(schedule_group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleSaveButton)
        button_layout.addWidget(self.scheduleResetButton)

        form.addWidget(button_row, 9, 1)
        form.addWidget(self.scheduleDeleteButton, 10, 1)
        schedule_layout.addLayout(form)
        return schedule_group

    def _build_dashboard_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        dashboard_group = QtWidgets.QGroupBox("主页数据看板", parent)
        dashboard_layout = QtWidgets.QVBoxLayout(dashboard_group)
        dashboard_layout.setContentsMargins(12, 22, 12, 12)
        dashboard_layout.setSpacing(10)

        self.dashboardSummaryLabel = QtWidgets.QLabel("统计: --", dashboard_group)
        self.dashboardSummaryLabel.setProperty("class", "infoChip")
        self.dashboardSummaryLabel.setStyleSheet("")
        self.dashboardConnectionLabel = QtWidgets.QLabel("连接概况: --", dashboard_group)
        self.dashboardConnectionLabel.setProperty("class", "infoChip")
        self.dashboardConnectionLabel.setStyleSheet("")
        self.dashboardModeLabel = QtWidgets.QLabel("模式切换: --", dashboard_group)
        self.dashboardModeLabel.setProperty("class", "infoChip")
        self.dashboardModeLabel.setStyleSheet("")
        self.dashboardWeatherLabel = QtWidgets.QLabel("天气刷新: --", dashboard_group)
        self.dashboardWeatherLabel.setProperty("class", "infoChip")
        self.dashboardWeatherLabel.setStyleSheet("")
        self.dashboardScheduleLabel = QtWidgets.QLabel("下次提醒: --", dashboard_group)
        self.dashboardScheduleLabel.setProperty("class", "infoChip")
        self.dashboardScheduleLabel.setStyleSheet("")
        self.dashboardTestLabel = QtWidgets.QLabel("自动测试: 未运行", dashboard_group)
        self.dashboardTestLabel.setProperty("class", "infoChip")
        self.dashboardTestLabel.setStyleSheet("")
        self.dashboardEventList = QtWidgets.QListWidget(dashboard_group)
        self.dashboardEventList.setMinimumHeight(140)

        dashboard_layout.addWidget(self.dashboardSummaryLabel)
        dashboard_layout.addWidget(self.dashboardConnectionLabel)
        dashboard_layout.addWidget(self.dashboardModeLabel)
        dashboard_layout.addWidget(self.dashboardWeatherLabel)
        dashboard_layout.addWidget(self.dashboardScheduleLabel)
        dashboard_layout.addWidget(self.dashboardTestLabel)
        dashboard_layout.addWidget(self.dashboardEventList)
        return dashboard_group

    def _build_alarm_schedule_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()
        outer.addWidget(self._build_board_alarm_group(host))
        outer.addWidget(self._build_schedule_management_group(host))
        outer.addStretch(1)
        return page

    def _build_debug_test_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()
        self._configure_sync_clock_group()
        outer.addWidget(self.ui.clockGroup)
        self.ui.demoGroup.setTitle("调试与协议")
        outer.addWidget(self.ui.demoGroup)

        test_group = QtWidgets.QGroupBox("自动化测试", host)
        test_layout = QtWidgets.QVBoxLayout(test_group)
        test_layout.setContentsMargins(12, 22, 12, 12)
        test_layout.setSpacing(10)
        self.runChecksButton = QtWidgets.QPushButton("一键运行联合测试", test_group)
        self.autoRunTestsCheck = QtWidgets.QCheckBox("启动后自动执行一次自动化测试", test_group)
        self.testStatusLabel = QtWidgets.QLabel("状态: 未运行", test_group)
        self.testStatusLabel.setProperty("class", "infoChip")
        self.testStatusLabel.setStyleSheet("")
        self.testExplainLabel = QtWidgets.QLabel(
            "覆盖 PING、SET/GET、日期时间写入、模式切换、天气协议、铃声协议与关键快捷键。",
            test_group,
        )
        self.testExplainLabel.setWordWrap(True)
        self.testExplainLabel.setProperty("class", "infoChip")
        self.testExplainLabel.setStyleSheet("")
        self.boardShortcutLabel = QtWidgets.QLabel(
            "板载快捷：USER1 短按切日夜；DISP 长按关显示并关 LED；EXT 用于退出/取消当前编辑或临时显示。",
            test_group,
        )
        self.boardShortcutLabel.setWordWrap(True)
        self.boardShortcutLabel.setProperty("class", "infoChip")
        self.boardShortcutLabel.setStyleSheet("")
        self.testOutputText = QtWidgets.QTextEdit(test_group)
        self.testOutputText.setReadOnly(True)
        self.testOutputText.setMinimumHeight(160)
        self.testOutputText.setPlaceholderText("测试输出会显示在这里。")
        test_layout.addWidget(self.runChecksButton)
        test_layout.addWidget(self.autoRunTestsCheck)
        test_layout.addWidget(self.testStatusLabel)
        test_layout.addWidget(self.testExplainLabel)
        test_layout.addWidget(self.boardShortcutLabel)
        test_layout.addWidget(self.testOutputText)

        ota_group = QtWidgets.QGroupBox("版本更新（预留）", host)
        ota_layout = QtWidgets.QVBoxLayout(ota_group)
        ota_layout.setContentsMargins(12, 22, 12, 12)
        ota_layout.setSpacing(8)
        self.otaPlaceholderLabel = QtWidgets.QLabel(
            "当前版本先保留 OTA 接口位置，后续可在这里接入 GitHub Release 更新流程。",
            ota_group,
        )
        self.otaPlaceholderLabel.setWordWrap(True)
        self.otaPlaceholderLabel.setProperty("class", "infoChip")
        self.otaPlaceholderLabel.setStyleSheet("")
        ota_layout.addWidget(self.otaPlaceholderLabel)

        outer.addWidget(test_group)
        outer.addWidget(ota_group)
        outer.addStretch(1)
        return page

    def _prepare_widgets(self) -> None:
        now = datetime.now()
        self.ui.dateEdit.setDate(QtCore.QDate(now.year, now.month, now.day))
        self.ui.timeEdit.setTime(QtCore.QTime(now.hour, now.minute, now.second))
        self.ui.alarmTimeEdit.setTime(QtCore.QTime(7, 30, 0))

        self.ui.displayToggleCombo.addItems(["ON", "OFF"])
        self.ui.formatCombo.addItems(["LEFT", "RIGHT"])
        self.ui.modeCombo.addItems(["DAY", "NIGHT"])
        for combo in (
            self.ui.displayToggleCombo,
            self.ui.formatCombo,
            self.ui.modeCombo,
        ):
            combo.setProperty("stateField", True)
            combo.setFocusPolicy(QtCore.Qt.NoFocus)
            combo.installEventFilter(self)
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

        self.ui.displayGroup.setTitle("系统设置")
        self.voiceEnabledCheck = QtWidgets.QCheckBox("启用语音播报", self.ui.displayGroup)
        self.ui.verticalLayout_3.addWidget(self.voiceEnabledCheck)
        outer.addWidget(self.ui.displayGroup)

        network_group = QtWidgets.QGroupBox("网络对时与天气", host)
        network_layout = QtWidgets.QGridLayout(network_group)
        network_layout.setContentsMargins(12, 22, 12, 12)
        network_layout.setHorizontalSpacing(10)
        network_layout.setVerticalSpacing(10)
        network_layout.setColumnStretch(1, 1)

        self.placeSlotCombo = QtWidgets.QComboBox(network_group)
        self.cityEdit = QtWidgets.QLineEdit(network_group)
        self.cityEdit.setPlaceholderText("城市名，例如 上海 / 成都 / Tokyo")
        self.lookupCityButton = QtWidgets.QPushButton("定位城市", network_group)
        self.saveExtensionConfigButton = QtWidgets.QPushButton("保存地点", network_group)
        self.syncWeatherApplyButton = QtWidgets.QPushButton(
            "一键对时、刷新天气并应用",
            network_group,
        )
        self.autoDayNightCheck = QtWidgets.QCheckBox("自动昼夜模式", network_group)
        self.themeFollowCheck = QtWidgets.QCheckBox("PC 主题跟随板端模式", network_group)
        self.cityInfoLabel = QtWidgets.QLabel("经纬度: -- | 时区: --", network_group)
        self.cityInfoLabel.setProperty("class", "infoChip")
        self.weatherInfoLabel = QtWidgets.QLabel("天气: 未刷新", network_group)
        self.weatherInfoLabel.setProperty("class", "infoChip")
        self.sunriseSunsetLabel = QtWidgets.QLabel("日出/日落: -- / --", network_group)
        self.sunriseSunsetLabel.setProperty("class", "infoChip")

        city_button_row = QtWidgets.QWidget(network_group)
        city_button_layout = QtWidgets.QHBoxLayout(city_button_row)
        city_button_layout.setContentsMargins(0, 0, 0, 0)
        city_button_layout.setSpacing(8)
        city_button_layout.addWidget(self.lookupCityButton)
        city_button_layout.addWidget(self.saveExtensionConfigButton)

        sync_button_row = QtWidgets.QWidget(network_group)
        sync_button_layout = QtWidgets.QHBoxLayout(sync_button_row)
        sync_button_layout.setContentsMargins(0, 0, 0, 0)
        sync_button_layout.setSpacing(8)
        sync_button_layout.addWidget(self.syncWeatherApplyButton)

        checkbox_row_1 = QtWidgets.QWidget(network_group)
        checkbox_row_1_layout = QtWidgets.QVBoxLayout(checkbox_row_1)
        checkbox_row_1_layout.setContentsMargins(0, 0, 0, 0)
        checkbox_row_1_layout.setSpacing(6)
        checkbox_row_1_layout.addWidget(self.autoDayNightCheck)
        checkbox_row_1_layout.addWidget(self.themeFollowCheck)

        network_layout.addWidget(QtWidgets.QLabel("地点"), 0, 0)
        network_layout.addWidget(self.placeSlotCombo, 0, 1)
        network_layout.addWidget(QtWidgets.QLabel("城市"), 1, 0)
        network_layout.addWidget(self.cityEdit, 1, 1)
        network_layout.addWidget(city_button_row, 2, 1)
        network_layout.addWidget(sync_button_row, 3, 1)
        network_layout.addWidget(checkbox_row_1, 4, 1)
        network_layout.addWidget(self.cityInfoLabel, 5, 1)
        network_layout.addWidget(self.weatherInfoLabel, 6, 1)
        network_layout.addWidget(self.sunriseSunsetLabel, 7, 1)

        ring_group = QtWidgets.QGroupBox("扩展提醒与铃声", host)
        ring_layout = QtWidgets.QGridLayout(ring_group)
        ring_layout.setContentsMargins(12, 22, 12, 12)
        ring_layout.setHorizontalSpacing(10)
        ring_layout.setVerticalSpacing(10)
        ring_layout.setColumnStretch(1, 1)

        self.ringPreviewCombo = QtWidgets.QComboBox(ring_group)
        self.ringPreviewCombo.addItems([label for _, label in self.ring_names])
        self.ringPreviewButton = QtWidgets.QPushButton("预览铃声", ring_group)
        self.quietNightCheck = QtWidgets.QCheckBox("夜间抑制扩展铃声", ring_group)
        self.themeModeLabel = QtWidgets.QLabel("主题状态: DAY", ring_group)
        self.themeModeLabel.setProperty("class", "infoChip")
        self.themeModeLabel.setStyleSheet("")
        self.ntpStatusLabel = QtWidgets.QLabel("最近 NTP: 未进行网络对时", ring_group)
        self.ntpStatusLabel.setProperty("class", "infoChip")
        self.ntpStatusLabel.setStyleSheet("")

        ring_layout.addWidget(QtWidgets.QLabel("铃声类型"), 0, 0)
        ring_layout.addWidget(self.ringPreviewCombo, 0, 1)
        ring_layout.addWidget(self.ringPreviewButton, 1, 1)
        ring_layout.addWidget(self.quietNightCheck, 2, 1)
        ring_layout.addWidget(self.themeModeLabel, 3, 1)
        ring_layout.addWidget(self.ntpStatusLabel, 4, 1)

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
        header = self.scheduleTable.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.scheduleTable.verticalHeader().setVisible(False)
        self.scheduleTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.scheduleTable.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.scheduleTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.scheduleTable.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scheduleTable.setMinimumHeight(180)
        schedule_layout.addWidget(self.scheduleTable)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

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
        form.addWidget(QtWidgets.QLabel("板端标签"), 1, 0)
        form.addWidget(self.scheduleBoardLabelEdit, 1, 1)
        form.addWidget(QtWidgets.QLabel("时间"), 2, 0)
        form.addWidget(self.scheduleTimeEdit, 2, 1)
        form.addWidget(QtWidgets.QLabel("规则"), 3, 0)
        form.addWidget(self.scheduleTypeCombo, 3, 1)
        form.addWidget(QtWidgets.QLabel("日期"), 4, 0)
        form.addWidget(self.scheduleDateEdit, 4, 1)
        form.addWidget(QtWidgets.QLabel("每周"), 5, 0)
        form.addWidget(weekday_host, 5, 1)
        form.addWidget(QtWidgets.QLabel("铃声"), 6, 0)
        form.addWidget(self.scheduleRingCombo, 6, 1)
        form.addWidget(QtWidgets.QLabel("语音"), 7, 0)
        form.addWidget(self.scheduleVoiceEdit, 7, 1)
        form.addWidget(self.scheduleEnabledCheck, 8, 1)

        button_row = QtWidgets.QWidget(schedule_group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleSaveButton)
        button_layout.addWidget(self.scheduleResetButton)

        form.addWidget(button_row, 9, 1)
        form.addWidget(self.scheduleDeleteButton, 10, 1)
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
        self.refresh_place_combo_labels()
        if not hasattr(self, "cityEdit"):
            return
        place = self._active_place()
        self.cityEdit.setText(place.name)
        self.placeSlotCombo.blockSignals(True)
        self.placeSlotCombo.setCurrentIndex(self.config.active_place_index)
        self.placeSlotCombo.blockSignals(False)
        self.autoDayNightCheck.setChecked(self.config.auto_day_night)
        self.themeFollowCheck.setChecked(self.config.theme_follow_mode)
        self.voiceEnabledCheck.setChecked(self.config.voice_enabled)
        self.quietNightCheck.setChecked(self.config.quiet_night_rings)
        if hasattr(self, "autoRunTestsCheck"):
            self.autoRunTestsCheck.setChecked(self.config.auto_run_tests_on_start)
        self.cityInfoLabel.setText(
            f"经纬度: {place.latitude:.4f}, {place.longitude:.4f} | 时区: {place.timezone}"
        )
        self.weatherInfoLabel.setText(f"天气: {self.weather_summary_text}")
        self.sunriseSunsetLabel.setText(
            f"日出/日落: {self.sunrise_text} / {self.sunset_text} | 当前时间: {self.last_selected_zone_time}"
        )
        self.ntpStatusLabel.setText(f"最近 NTP: {self.last_ntp_sync_text}")
        if hasattr(self, "autoModeNoticeLabel") and self.config.auto_day_night:
            self.autoModeNoticeLabel.clear()
            self.autoModeNoticeLabel.setVisible(False)
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
        now = self._selected_zone_now().replace(tzinfo=None)
        place = self._active_place()
        enabled_count = len([item for item in self.schedules if item.enabled])
        next_schedule_text, next_schedule_time = self._next_reminder_summary(now)
        expected_mode = (
            "DAY" if should_use_day_mode(now, self.last_weather_snapshot) else "NIGHT"
        ) if self.last_weather_snapshot is not None else self.last_mode
        weather_text = self.weather_summary_text
        if self.last_weather_snapshot is not None:
            weather_text = format_weather_summary(
                self.last_weather_snapshot.weather_code,
                self.last_weather_snapshot.temperature_c,
            )
        self.dashboardSummaryLabel.setText(
            f"统计: 共 {len(self.schedules)} 条提醒，启用 {enabled_count} 条 | 当前城市 {place.name}"
        )
        self.dashboardConnectionLabel.setText(
            f"连接概况: {'已连接 ' + self.ui.portCombo.currentText() if self.is_connected else '未连接'} | 最近 NTP {self.last_ntp_sync_text}"
        )
        self.dashboardModeLabel.setText(
            f"模式切换: 当前 {self.last_mode} | 当前所处 {'日间' if expected_mode == 'DAY' else '夜间'} | 自动昼夜 {'开' if self.config.auto_day_night else '关'}"
        )
        self.dashboardWeatherLabel.setText(
            f"天气刷新: {weather_text} | 日出 {self.sunrise_text} | 日落 {self.sunset_text} | {place.timezone}"
        )
        self.dashboardScheduleLabel.setText(
            f"下次提醒: {next_schedule_text} | 剩余 {self._format_countdown(next_schedule_time, now)}"
        )
        self.dashboardTestLabel.setText(
            f"自动测试: {self.last_test_summary} | 当前城市时间 {self.last_selected_zone_time or now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.dashboardEventList.clear()
        for entry in load_recent_event_logs(APP_DIR, limit=10):
            when = entry.get("when", "--")
            kind = entry.get("kind", "event")
            detail = entry.get("detail", "")
            self.dashboardEventList.addItem(f"{when} | {kind} | {detail}")

    def _wire_signals(self) -> None:
        self.ui.refreshPortsButton.clicked.connect(self.refresh_ports)
        self.ui.connectButton.clicked.connect(self.connect_and_apply_port)
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
        self.placeSlotCombo.currentIndexChanged.connect(self.select_saved_place)
        self.lookupCityButton.clicked.connect(self.lookup_city)
        self.saveExtensionConfigButton.clicked.connect(self.save_extension_config)
        self.syncWeatherApplyButton.clicked.connect(
            lambda: self.sync_weather_and_apply(trigger_source="按钮", run_tests=False)
        )
        self.ringPreviewButton.clicked.connect(self.preview_ring)
        self.scheduleApplyAlarmButton.clicked.connect(self.apply_schedule_alarm)
        self.scheduleDisableAlarmButton.clicked.connect(
            lambda: self.send_command("*SET:ALARM OFF")
        )
        self.scheduleQueryAlarmButton.clicked.connect(
            lambda: self.send_command("*GET:ALARM", "ALARM")
        )
        self.scheduleSaveButton.clicked.connect(self.save_schedule_item)
        self.scheduleResetButton.clicked.connect(self.reset_schedule_form)
        self.scheduleDeleteButton.clicked.connect(self.delete_selected_schedule)
        self.scheduleTable.itemSelectionChanged.connect(self.load_selected_schedule)
        self.scheduleTypeCombo.currentIndexChanged.connect(self._sync_schedule_type_ui)
        self.runChecksButton.clicked.connect(self.run_automated_checks)
        self.autoRunTestsCheck.toggled.connect(lambda _checked: self.save_extension_config(log_message=False))

    def extension_tick(self) -> None:
        now = datetime.now()
        zone_now = self._selected_zone_now().replace(tzinfo=None)
        self.refresh_place_combo_labels()
        if self.config.auto_day_night and self.last_weather_refresh_at is not None:
            if zone_now.second == 0:
                self.apply_auto_day_night(zone_now)

        if self.last_weather_refresh_at is None or (
            now - self.last_weather_refresh_at
        ) >= timedelta(minutes=max(5, self.config.weather_refresh_minutes)):
            self.refresh_weather_and_push(log_trigger=False)

        schedule_changed = False
        for item in self.schedules:
            if schedule_trigger_matches(item, zone_now):
                self.trigger_schedule(item)
                mark_schedule_triggered(item, zone_now)
                schedule_changed = True
        if schedule_changed:
            save_schedules(APP_DIR, self.schedules)
            self.refresh_schedule_table()
            self.refresh_dashboard()

    def select_saved_place(self, index: int) -> None:
        if not (0 <= index < len(self.config.saved_places)):
            return
        self.config.active_place_index = index
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self.refresh_dashboard()

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
        place = self._active_place()
        place.name = result.name
        place.latitude = result.latitude
        place.longitude = result.longitude
        place.timezone = result.timezone
        self.config.saved_places[self.config.active_place_index] = place
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self.log("INFO", f"已定位城市: {result.name} ({result.latitude:.4f}, {result.longitude:.4f})")
        self.refresh_weather_and_push()

    def save_extension_config(self, log_message: bool = True) -> None:
        place = self._active_place()
        place.name = self.cityEdit.text().strip() or place.name
        self.config.saved_places[self.config.active_place_index] = place
        self.config.auto_day_night = self.autoDayNightCheck.isChecked()
        self.config.theme_follow_mode = self.themeFollowCheck.isChecked()
        self.config.voice_enabled = self.voiceEnabledCheck.isChecked()
        self.config.quiet_night_rings = self.quietNightCheck.isChecked()
        if hasattr(self, "autoRunTestsCheck"):
            self.config.auto_run_tests_on_start = self.autoRunTestsCheck.isChecked()
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self._refresh_theme_from_mode()
        if self.config.auto_day_night:
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        self.refresh_dashboard()
        if log_message:
            self.log("INFO", "扩展配置已保存。")

    def _send_datetime_snapshot(self, moment: datetime, source_text: str) -> None:
        if self.sync_in_progress:
            return
        self.sync_snapshot = moment.replace(microsecond=0)
        self.sync_in_progress = True
        self.ui.syncNowButton.setEnabled(False)
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
        self.ui.dateEdit.setDate(
            QtCore.QDate(self.sync_snapshot.year, self.sync_snapshot.month, self.sync_snapshot.day)
        )
        self.ui.timeEdit.setTime(
            QtCore.QTime(self.sync_snapshot.hour, self.sync_snapshot.minute, self.sync_snapshot.second)
        )
        self.send_command(build_set_date_command(self.sync_snapshot))
        QtCore.QTimer.singleShot(220, self._sync_host_time_step2)
        self.last_ntp_sync_text = source_text
        self.last_selected_zone_time = self.sync_snapshot.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, "ntpStatusLabel"):
            self.ntpStatusLabel.setText(f"最近 NTP: {source_text}")

    def sync_ntp_time(self, trigger_source: str = "按钮") -> None:
        try:
            snapshot_utc = fetch_ntp_time(self.config.ntp_host)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"NTP 对时失败: {exc}")
            if "启动" in trigger_source or trigger_source == "USER1":
                self.log("WARN", "板端启动后若仍停在默认时间，可视为本次 NTP 对时失败。")
            append_event_log(APP_DIR, "ntp_error", str(exc))
            self.refresh_dashboard()
            return
        snapshot = self._selected_zone_now(snapshot_utc).replace(tzinfo=None)
        source_text = f"{snapshot.strftime('%Y-%m-%d %H:%M:%S')} @ {self._active_place().name}"
        self._send_datetime_snapshot(snapshot, source_text)
        append_event_log(APP_DIR, "ntp_sync", f"{trigger_source} -> {source_text}")
        self.log("INFO", f"NTP 对时成功并写入 S800（来源: {trigger_source}）。")
        self.refresh_dashboard()

    def sync_weather_and_apply(
        self,
        trigger_source: str = "按钮",
        run_tests: bool | None = None,
    ) -> None:
        self.save_extension_config(log_message=False)
        self.pending_auto_test_after_apply = (
            self.config.auto_run_tests_on_start if run_tests is None else run_tests
        )
        self.sync_ntp_time(trigger_source=trigger_source)
        self.refresh_weather_and_push(log_trigger=True)

    def refresh_weather_and_push(self, log_trigger: bool = True) -> None:
        if self.weather_refresh_in_progress:
            return
        self.weather_refresh_in_progress = True
        place = self._active_place()
        city_name = place.name
        latitude = place.latitude
        longitude = place.longitude
        timezone_name = place.timezone

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
        self.weather_summary_text = format_weather_summary(
            snapshot.weather_code, snapshot.temperature_c
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
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        if self.pending_auto_test_after_apply:
            self.pending_auto_test_after_apply = False
            QtCore.QTimer.singleShot(500, self.run_automated_checks)

    def apply_auto_day_night(self, now: datetime, force_apply: bool = False) -> None:
        if self.last_weather_snapshot is None:
            return
        expected_mode = (
            "DAY" if should_use_day_mode(now, self.last_weather_snapshot) else "NIGHT"
        )
        self.last_mode_expected = expected_mode
        if (
            not force_apply
            and expected_mode == self.last_mode_auto_applied
            and expected_mode == self.last_mode
        ):
            return
        self.last_mode_auto_applied = expected_mode
        self._remember_mode_request(expected_mode, "auto")
        if self.is_connected:
            self.send_command(f"*SET:MODE {expected_mode}")
        self.last_mode = expected_mode
        self._refresh_theme_from_mode()
        append_event_log(APP_DIR, "auto_mode", expected_mode)
        self.refresh_dashboard()

    def preview_ring(self) -> None:
        ring_name = self.ring_names[self.ringPreviewCombo.currentIndex()][0]
        self._play_ring_or_fallback(ring_name, "预览铃声")
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
            target_date=target_date if schedule_type == "once" else "",
            ring_type=ring_type,
            enabled=self.scheduleEnabledCheck.isChecked(),
            voice_text=voice_text or "",
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
                self._play_ring_or_fallback(item.ring_type, f"提醒 {item.title}")
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

    def _open_port(self, port_name: str) -> bool:
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
            self.refresh_dashboard()
            return False

        self.status_connection.setText(f"连接: {port_name}")
        self.poll_timer.start()
        self.ping_timer.start()
        self.board_ready_seen = False
        self.log("INFO", f"已连接 {port_name}")
        self.refresh_dashboard()
        return True

    def connect_port(self) -> bool:
        port_name = self.ui.portCombo.currentText().strip()
        if not port_name:
            self.log("WARN", "没有可连接的 COM 口。")
            return False
        if not self._open_port(port_name):
            return False
        self.query_runtime_state()
        if self.cached_weather_text:
            self.send_command(
                build_set_weather_command(self.cached_weather_text, self.cached_weather_led_mask)
            )
        return True

    def connect_and_apply_port(self) -> None:
        if not self.connect_port():
            return
        self.save_extension_config(log_message=False)
        self._remember_mode_request(self.ui.modeCombo.currentText(), "manual_ui")
        self.send_command(f"*SET:DISPLAY {self.ui.displayToggleCombo.currentText()}")
        self.send_command(f"*SET:FORMAT {self.ui.formatCombo.currentText()}")
        if self.config.auto_day_night:
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        else:
            self.send_command(f"*SET:MODE {self.ui.modeCombo.currentText()}")
        self.sync_weather_and_apply(
            trigger_source="连接并应用",
            run_tests=self.config.auto_run_tests_on_start,
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
        self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        if log_message:
            self.log("INFO", "串口已断开。")
        self.refresh_dashboard()

    def query_runtime_state(self) -> None:
        self.send_command("*GET:DATE", "DATE")
        self.send_command("*GET:TIME", "TIME")
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
        self.last_tx_command = cleaned
        self.last_tx_monotonic = time.perf_counter()
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
        raw = line.strip()
        if raw.upper() == "S800 CLOCK READY":
            self.log("RX", raw)
            self.board_ready_seen = True
            if (time.monotonic() - self.last_ready_sync_monotonic) > 2.5:
                self.last_ready_sync_monotonic = time.monotonic()
                self.log("INFO", "检测到板端启动完成，自动执行一次对时、天气刷新和模式同步。")
                self.sync_weather_and_apply(
                    trigger_source="板端启动",
                    run_tests=self.config.auto_run_tests_on_start,
                )
            return
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
            if self._handle_protocol_error(parsed):
                return
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
            next_mode = parsed.data.strip() or "DAY"
            mode_origin = self._consume_mode_request(next_mode)
            self.last_mode = next_mode
            self.status_mode.setText(f"MODE: {self.last_mode}")
            self._refresh_theme_from_mode()
            append_event_log(APP_DIR, "mode", self.last_mode)
            if self.config.auto_day_night and mode_origin != "auto":
                expected_mode = self.last_mode_expected
                if (
                    not expected_mode
                    and self.last_weather_snapshot is not None
                ):
                    expected_mode = (
                        "DAY"
                        if should_use_day_mode(
                            self._selected_zone_now().replace(tzinfo=None),
                            self.last_weather_snapshot,
                        )
                        else "NIGHT"
                    )
                if expected_mode and self.last_mode != expected_mode:
                    source_text = "上位机" if mode_origin == "manual_ui" else "板端"
                    self._disable_auto_day_night_due_to_manual(source_text, self.last_mode)
            self.refresh_dashboard()
            self._set_latest_event(f"模式切换 -> {self.last_mode}")
            return

        if parsed.name == "KEY":
            key = parsed.data.strip().upper()
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
        if query == "DATE" and data:
            normalized = data.replace(".", "")
            if len(normalized) == 6 and normalized.isdigit():
                year = 2000 + int(normalized[0:2])
                month = int(normalized[2:4])
                day = int(normalized[4:6])
                qdate = QtCore.QDate(year, month, day)
                if qdate.isValid():
                    self.ui.dateEdit.setDate(qdate)
        elif query == "TIME" and data:
            normalized = data.replace(".", ":")
            qtime = QtCore.QTime.fromString(normalized, "HH:mm:ss")
            if qtime.isValid():
                self.ui.timeEdit.setTime(qtime)
        elif query == "FORMAT" and data:
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
            if data and data != "OFF":
                alarm_time = QtCore.QTime.fromString(data.replace(".", ":"), "HH:mm:ss")
                if alarm_time.isValid():
                    self.ui.alarmTimeEdit.setTime(alarm_time)
                    if hasattr(self, "scheduleAlarmTimeEdit"):
                        self.scheduleAlarmTimeEdit.setTime(alarm_time)
        elif query == "DISPLAY" and data in {"ON", "OFF"}:
            self.ui.displayToggleCombo.setCurrentText(data)
        self.refresh_dashboard()

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
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(True)
        self.refresh_dashboard()

    def apply_display_state(self) -> None:
        next_value = "OFF" if self.ui.displayToggleCombo.currentText() == "ON" else "ON"
        self.ui.displayToggleCombo.setCurrentText(next_value)
        self.send_command(f"*SET:DISPLAY {next_value}")

    def apply_format(self) -> None:
        value = "RIGHT" if self.ui.formatCombo.currentText() == "LEFT" else "LEFT"
        self.ui.formatCombo.setCurrentText(value)
        self.send_command(f"*SET:FORMAT {value}")
        QtCore.QTimer.singleShot(
            180, lambda: self.send_command("*GET:FORMAT", "FORMAT")
        )

    def apply_mode(self) -> None:
        value = "NIGHT" if self.ui.modeCombo.currentText() == "DAY" else "DAY"
        self.ui.modeCombo.setCurrentText(value)
        if self.config.auto_day_night:
            expected_mode = self.last_mode_expected
            if (
                not expected_mode
                and self.last_weather_snapshot is not None
            ):
                expected_mode = (
                    "DAY"
                    if should_use_day_mode(
                        self._selected_zone_now().replace(tzinfo=None),
                        self.last_weather_snapshot,
                    )
                    else "NIGHT"
                )
            if expected_mode and value != expected_mode:
                self._disable_auto_day_night_due_to_manual("上位机", value)
        self._remember_mode_request(value, "manual_ui")
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

    def apply_schedule_alarm(self) -> None:
        time_value = self.scheduleAlarmTimeEdit.time()
        self.ui.alarmTimeEdit.setTime(time_value)
        self.apply_alarm()

    def run_automated_checks(self) -> None:
        if self.test_run_in_progress:
            return
        port_name = self.ui.portCombo.currentText().strip()
        if not port_name and not self.is_connected:
            self.log("WARN", "没有可测试的 COM 口。")
            return
        self.test_run_in_progress = True
        self.runChecksButton.setEnabled(False)
        self.testStatusLabel.setText("状态: 运行中")
        active_port = (
            self.serial_port.port
            if self.is_connected and self.serial_port is not None
            else port_name
        )
        self.testOutputText.setPlainText(f"正在对 {active_port} 执行联合测试...\n")
        if self.is_connected:
            self.poll_timer.stop()
            self.ping_timer.stop()
            self.read_buffer = ""

        def worker() -> None:
            try:
                if self.is_connected and self.serial_port is not None:
                    ok, output = execute_checks_on_open_port(self.serial_port)
                else:
                    ok, output = execute_checks_on_port(active_port)
            except Exception as exc:  # noqa: BLE001
                output = f"FAIL\n{exc}"
                ok = False
            self.test_run_finished.emit(output.strip(), ok)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_test_run(self, output: str, ok: bool) -> None:
        self.test_run_in_progress = False
        self.runChecksButton.setEnabled(True)
        self.last_test_ok = ok
        self.last_test_summary = "PASS" if ok else "FAIL"
        self.testStatusLabel.setText(f"状态: {'通过' if ok else '失败'}")
        self.testOutputText.setPlainText(output or ("PASS" if ok else "FAIL"))
        if self.is_connected:
            self.poll_timer.start()
            self.ping_timer.start()
            self.query_runtime_state()
        append_event_log(APP_DIR, "test_run", self.last_test_summary)
        self.refresh_dashboard()
        self.log("INFO" if ok else "WARN", f"联合测试完成: {self.last_test_summary}")

    def _remember_mode_request(self, mode_value: str, origin: str) -> None:
        self.pending_mode_origin = origin
        self.pending_mode_value = mode_value.strip().upper()
        self.pending_mode_deadline = time.monotonic() + 2.5

    def _consume_mode_request(self, mode_value: str) -> str:
        current = time.monotonic()
        if self.pending_mode_deadline and current > self.pending_mode_deadline:
            self.pending_mode_origin = ""
            self.pending_mode_value = ""
            self.pending_mode_deadline = 0.0
            return ""
        if (
            self.pending_mode_deadline
            and self.pending_mode_value
            and mode_value.strip().upper() == self.pending_mode_value
        ):
            origin = self.pending_mode_origin
            self.pending_mode_origin = ""
            self.pending_mode_value = ""
            self.pending_mode_deadline = 0.0
            return origin
        return ""

    def _disable_auto_day_night_due_to_manual(self, source_text: str, mode_value: str) -> None:
        self.config.auto_day_night = False
        save_config(APP_DIR, self.config)
        message = f"检测到{source_text}手动切换到 {mode_value}，已自动关闭自动昼夜模式。"
        if hasattr(self, "autoModeNoticeLabel"):
            self.autoModeNoticeLabel.setText(message)
            self.autoModeNoticeLabel.setVisible(True)
        if hasattr(self, "autoDayNightCheck"):
            self.autoDayNightCheck.setChecked(False)
        append_event_log(APP_DIR, "auto_mode_disabled", message)
        self.log("INFO", message)
        self.refresh_dashboard()

    def _ring_fallback_duration(self, ring_name: str) -> int:
        return {
            "DEFAULT": 500,
            "WORK_START": 350,
            "WORK_END": 220,
            "WAKE": 1200,
            "SONG": 800,
        }.get(ring_name.upper(), 500)

    def _play_ring_or_fallback(self, ring_name: str, source_text: str) -> None:
        if self.ring_command_supported is False:
            self.send_command(f"*SET:BEEP {self._ring_fallback_duration(ring_name)}")
            self.log("INFO", f"{source_text} 使用蜂鸣兼容模式。")
            return
        self.send_command(build_set_ring_command(ring_name))

    def _handle_protocol_error(self, parsed: ParsedLine) -> bool:
        error_text = (parsed.data or parsed.name or "").strip().upper()
        if error_text != "PARAM":
            return False
        if (
            self.last_tx_command.upper().startswith("*SET:RING ")
            and (time.perf_counter() - self.last_tx_monotonic) < 1.5
        ):
            self.ring_command_supported = False
            ring_name = self.last_tx_command.strip().split()[-1].upper()
            message = "当前板端未启用扩展铃声协议，已自动回退到基础蜂鸣兼容模式。"
            append_event_log(APP_DIR, "ring_fallback", message)
            self.log("WARN", message)
            self.send_command(f"*SET:BEEP {self._ring_fallback_duration(ring_name)}")
            self.refresh_dashboard()
            return True
        return False

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
