from __future__ import annotations

import html
import os
import random
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bootstrap_qt import configure_qt_runtime

APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
QT_RUNTIME = configure_qt_runtime(APP_DIR)
APP_VERSION = "v2.1"
GITHUB_URL = "https://github.com/Cyh29hao"
LOGO_PATH = BUNDLE_DIR / "assets" / "clock_logo.svg"
ICON_PATH = BUNDLE_DIR / "assets" / "clock_logo.ico"
LOCAL_MODE_LABEL = "不使用串口"
LED_BIT_LABELS = [
    ("D1", "心跳"),
    ("D2", "闹钟"),
    ("D3", "编辑"),
    ("D4", "串口RX"),
    ("D5", "串口TX"),
    ("D6", "夜间"),
    ("D7", "RIGHT"),
    ("D8", "手动覆盖"),
]


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes = __import__("ctypes")
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Cyh29hao.SmartClockHost.v21"
        )
    except Exception:
        pass

import serial
from PyQt5 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from extension_services import (
    build_weather_led_mask,
    build_weather_token,
    format_utc_offset,
    format_weather_summary,
    fetch_ntp_time,
    fetch_weather_snapshot,
    geocode_city,
    infer_timezone_offset_seconds,
    should_use_day_mode,
    speak_text,
    timezone_now,
    weather_emoji,
    weather_code_summary,
)
from extension_store import (
    AppConfig,
    RuntimeState,
    SavedPlace,
    ScheduleItem,
    append_event_log,
    ensure_storage,
    load_config,
    load_recent_event_logs,
    load_runtime_state,
    load_schedules,
    mark_schedule_triggered,
    normalize_board_message,
    normalize_board_token,
    parse_clock_hms,
    save_config,
    save_runtime_state,
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
    token_to_text,
)
from run_extension_checks import (
    estimated_duration_seconds,
    execute_checks_on_open_port,
    execute_checks_on_port,
    execute_host_only_checks,
)
from twin_widgets import DigitalTwinWidget
from ui_main import Ui_MainWindow


class SmartClockArrowStyle(QtWidgets.QProxyStyle):
    """Draw stable combo/drop-down arrows without relying on image assets."""

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802 - Qt API
        if element == QtWidgets.QStyle.PE_IndicatorArrowDown and option is not None:
            rect = option.rect
            if rect.isValid():
                enabled = bool(option.state & QtWidgets.QStyle.State_Enabled)
                color_group = (
                    QtGui.QPalette.Active if enabled else QtGui.QPalette.Disabled
                )
                color = option.palette.color(color_group, QtGui.QPalette.Text)
                if not enabled:
                    color.setAlpha(140)

                painter.save()
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                pen_width = max(2.0, min(rect.width(), rect.height()) * 0.13)
                painter.setPen(
                    QtGui.QPen(
                        color,
                        pen_width,
                        QtCore.Qt.SolidLine,
                        QtCore.Qt.RoundCap,
                        QtCore.Qt.RoundJoin,
                    )
                )
                center = rect.center()
                half = max(4.0, min(rect.width(), rect.height()) * 0.24)
                y_top = center.y() - half * 0.30
                y_bottom = center.y() + half * 0.42
                points = QtGui.QPolygonF(
                    [
                        QtCore.QPointF(center.x() - half, y_top),
                        QtCore.QPointF(center.x(), y_bottom),
                        QtCore.QPointF(center.x() + half, y_top),
                    ]
                )
                painter.drawPolyline(points)
                painter.restore()
                return
        super().drawPrimitive(element, option, painter, widget)


def install_stable_arrow_style() -> None:
    app = QtWidgets.QApplication.instance()
    if app is None or hasattr(app, "_smart_clock_arrow_style"):
        return
    style = SmartClockArrowStyle(app.style())
    app._smart_clock_arrow_style = style
    app.setStyle(style)


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
    weather_refresh_finished = QtCore.pyqtSignal(object, object, bool, int, object)
    ntp_sync_finished = QtCore.pyqtSignal(object, object, str, int)
    test_point_finished = QtCore.pyqtSignal(str)
    test_run_finished = QtCore.pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        install_stable_arrow_style()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        ensure_storage(APP_DIR)
        self.config: AppConfig = load_config(APP_DIR)
        self.runtime_state: RuntimeState = load_runtime_state(APP_DIR)
        self.schedules: list[ScheduleItem] = load_schedules(APP_DIR)
        self.log_dir = APP_DIR / "logs"
        self.setWindowTitle(f"智能联网时钟系统 - PC 上位机 {APP_VERSION}")
        icon_path = ICON_PATH if ICON_PATH.exists() else LOGO_PATH
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self.serial_port: serial.Serial | None = None
        self.local_mode_active = False
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
        self.last_dashboard_minute = ""
        self.last_display_event: tuple[str, int] | None = None
        self.last_led_event: int | None = None
        self.last_board_display_monotonic = 0.0
        self.last_serial_rx_monotonic = 0.0
        self.last_serial_recovery_monotonic = 0.0
        self.serial_recovery_stage = 0
        self.last_user1_ntp_request_monotonic = 0.0
        self.last_user1_ntp_warn_monotonic = 0.0
        self.last_user2_replay_monotonic = 0.0
        self.last_user2_trigger_monotonic = 0.0
        self.pending_user2_display_text = ""
        self.pending_user2_display_deadline = 0.0
        self.pending_user2_display_fallback_done = False
        self.pending_user2_display_retry_count = 0
        self.board_weather_cache_token = ""
        self.board_weather_cache_led_mask = -1
        self.board_weather_cache_synced_monotonic = 0.0
        self.manual_ping_log_until = 0.0
        self.single_alarm_query_generation = 0
        self.latest_display_text = "--"
        self.latest_led_text = "--"
        self.latest_event_text = "等待数据"
        self.max_log_blocks = 400
        self.sync_in_progress = False
        self.ntp_fetch_in_progress = False
        self.sync_snapshot: datetime | None = None
        self.sync_watchdog_token = 0
        self.sync_write_phase = ""
        self.sync_date_retry_done = False
        self.ntp_watchdog_token = 0
        self.weather_watchdog_token = 0
        self.weather_timeout_ntp_source = ""
        self.ntp_fallback_on_fail = False
        self.ntp_query_after = False
        self.ntp_active_source = ""
        self.sync_query_after_finish = False
        self.pending_lifecycle_ntp_source = ""
        self.pending_lifecycle_ntp_query_after = False
        self.pending_lifecycle_ntp_fallback = False
        self.last_lifecycle_ntp_monotonic = 0.0
        self.auto_serial_quiet_until = 0.0
        self.auto_serial_quiet_reason = ""
        self.pending_soft_reset_sync = False
        self.soft_reset_deadline_monotonic = 0.0
        self.runtime_shadow_base_iso = ""
        self.runtime_shadow_base_datetime: datetime | None = None
        self.runtime_shadow_base_monotonic = 0.0
        self.weather_refresh_in_progress = False
        self.last_weather_refresh_at: datetime | None = None
        self.last_mode_auto_applied = ""
        self.last_tx_command = ""
        self.last_tx_monotonic = 0.0
        self.last_night_display_fix_monotonic = 0.0
        self.ring_command_supported: bool | None = None
        self.last_test_summary = "未运行"
        self.last_test_ok = False
        self.test_run_in_progress = False
        self.test_run_started_at = 0.0
        self.test_run_full = False
        self.test_cancel_event: threading.Event | None = None
        self.test_saved_section_index: int | None = None
        self.pending_auto_test_after_apply = False
        self.pending_auto_test_full = False
        self.last_apply_monotonic = 0.0
        self.last_ready_sync_monotonic = 0.0
        self.last_mode_expected = ""
        self.pending_mode_origin = ""
        self.pending_mode_value = ""
        self.pending_mode_deadline = 0.0
        self.last_mode_resync_monotonic = 0.0
        self.mode_resync_guard_until = 0.0
        self.board_ready_seen = False
        self.local_display_override_text = ""
        self.local_display_override_started = 0.0
        self.local_display_override_until = 0.0
        self.local_display_override_led_mask: int | None = None
        self.local_view_scroll_key = ""
        self.local_view_scroll_started = 0.0
        self.boot_mirror_generation = 0
        self.startup_sync_pending = False
        self._startup_theme_polished = False
        self.syncing_extension_widgets = False
        self.preferred_port_name = ""
        self.manual_port_choice_made = False
        self.serial_io_lock = threading.RLock()
        self.test_saved_auto_day_night: bool | None = None
        self.test_saved_runtime_state: RuntimeState | None = None
        self.key_command_guard_until = 0.0
        self.key_command_last_log_monotonic = 0.0
        self.last_key_command_name = ""
        self.last_key_command_monotonic = 0.0
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
        self._sanitize_runtime_state()
        self._apply_theme()
        self._prepare_widgets()
        self._refine_layout()
        self._wire_signals()
        self.weather_refresh_finished.connect(self._finish_weather_refresh)
        self.ntp_sync_finished.connect(self._finish_ntp_sync)
        self.test_point_finished.connect(self._append_test_output_line)
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
        QtCore.QTimer.singleShot(0, self._finalize_startup_theme)
        QtCore.QTimer.singleShot(180, self._finalize_startup_theme)
        self.log("INFO", "PC 上位机已启动，等待连接 S800。")

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._startup_theme_polished:
            self._finalize_startup_theme()
        QtCore.QTimer.singleShot(0, self._enforce_main_splitter_layout)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "mainSplitter"):
            QtCore.QTimer.singleShot(0, self._enforce_main_splitter_layout)
        if hasattr(self, "status_features") and not self.test_run_in_progress:
            self._restore_footer_features()

    def _footer_feature_text(self) -> str:
        if self.width() >= 1500:
            return (
                "智能联网时钟系统 | 串口同步·数字孪生 | NTP天气·全球时区 | "
                "板载闹钟·PC日程管理 | 个性化界面·清晰面板 | 自动测试·稳定鲁棒"
            )
        return "智能联网时钟系统 | 串口孪生 | NTP天气 | 全球时区 | 闹钟日程 | 个性面板 | 自动测试"

    def _restore_footer_features(self) -> None:
        if hasattr(self, "status_features"):
            self.status_features.setText(self._footer_feature_text())

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if (
            hasattr(self, "scheduleTable")
            and obj is self.scheduleTable.viewport()
            and event.type() == QtCore.QEvent.MouseButtonPress
            and self.scheduleTable.indexAt(event.pos()).isValid() is False
        ):
            QtCore.QTimer.singleShot(0, self.reset_schedule_form)
        return super().eventFilter(obj, event)

    def _right_panel_width_for(self, total_width: int) -> int:
        if total_width < 980:
            return 380
        if total_width < 1180:
            return 420
        if total_width < 1380:
            return 450
        return 500

    def _enforce_main_splitter_layout(self) -> None:
        splitter = getattr(self, "mainSplitter", None)
        right_panel = getattr(self, "rightPanel", None)
        left_panel = getattr(self, "leftPanel", None)
        if splitter is None or right_panel is None or left_panel is None:
            return

        available = max(0, splitter.width() - splitter.handleWidth())
        if available <= 0:
            return

        right_width = self._right_panel_width_for(available)
        if available - right_width < 360:
            right_width = max(360, available - 360)
        right_width = max(360, min(500, right_width))
        left_width = max(360, available - right_width)

        right_panel.setMinimumWidth(right_width)
        right_panel.setMaximumWidth(right_width)
        left_panel.setMinimumWidth(360)
        splitter.setSizes([left_width, right_width])

    def _finalize_startup_theme(self) -> None:
        self._refresh_theme_from_mode()
        self._startup_theme_polished = True

    def _build_statusbar(self) -> None:
        self.ui.statusbar.setSizeGripEnabled(False)
        self.ui.statusbar.setMaximumHeight(34)
        self.status_project = QtWidgets.QLabel("智能时钟")
        self.status_project.setContentsMargins(4, 0, 8, 0)
        self.status_project_icon = QtWidgets.QLabel()
        if LOGO_PATH.exists():
            pixmap = QtGui.QPixmap(str(LOGO_PATH)).scaled(
                18,
                18,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self.status_project_icon.setPixmap(pixmap)
        self.status_features = QtWidgets.QLabel(self._footer_feature_text())
        self.status_features.setMinimumWidth(0)
        self.status_features.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.status_connection = QtWidgets.QLabel("未连接")
        self.status_mode = QtWidgets.QLabel("DAY")
        self.status_location = QtWidgets.QLabel("上海")
        self.status_local_time = QtWidgets.QLabel("--:--:--")
        self.status_latency = QtWidgets.QLabel("-- ms")
        self.status_version = QtWidgets.QLabel(APP_VERSION)
        self.status_developer = QtWidgets.QLabel("Cyh29hao")
        self.status_clear_button = QtWidgets.QToolButton(self)
        self.status_clear_button.setObjectName("statusActionButton")
        self.status_clear_button.setText("清空")
        self.status_export_button = QtWidgets.QToolButton(self)
        self.status_export_button.setObjectName("statusActionButton")
        self.status_export_button.setText("导出")
        self.status_github_button = QtWidgets.QToolButton(self)
        self.status_github_button.setObjectName("statusActionButton")
        self.status_github_button.setText("GitHub")
        self.status_github_button.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_URL))
        )
        self.status_clear_button.clicked.connect(self.ui.logTextEdit.clear)
        self.status_export_button.clicked.connect(self.export_log)
        for label in (
            self.status_project,
            self.status_connection,
            self.status_mode,
            self.status_location,
            self.status_local_time,
            self.status_latency,
            self.status_version,
            self.status_developer,
        ):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        for button in (
            self.status_clear_button,
            self.status_export_button,
            self.status_github_button,
        ):
            button.setMinimumWidth(52)
            button.setMaximumWidth(72)
            button.setMinimumHeight(24)
            button.setMaximumHeight(28)
        self.ui.statusbar.addWidget(self.status_project_icon)
        self.ui.statusbar.addWidget(self.status_project)
        self.ui.statusbar.addWidget(self.status_features, 1)
        self.ui.statusbar.addPermanentWidget(self.status_connection)
        self.ui.statusbar.addPermanentWidget(self.status_mode)
        self.ui.statusbar.addPermanentWidget(self.status_location)
        self.ui.statusbar.addPermanentWidget(self.status_local_time)
        self.ui.statusbar.addPermanentWidget(self.status_latency)
        self.ui.statusbar.addPermanentWidget(self.status_version)
        self.ui.statusbar.addPermanentWidget(self.status_developer)
        self.ui.statusbar.addPermanentWidget(self.status_clear_button)
        self.ui.statusbar.addPermanentWidget(self.status_export_button)
        self.ui.statusbar.addPermanentWidget(self.status_github_button)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Wheel:
            if isinstance(watched, QtWidgets.QComboBox):
                view = watched.view()
                if view is not None and view.isVisible():
                    return False
                return True
            if isinstance(watched, QtWidgets.QAbstractSpinBox):
                return True
        return super().eventFilter(watched, event)

    def _active_place(self) -> SavedPlace:
        return self.config.saved_places[self.config.active_place_index]

    def _is_local_mode_selected(self) -> bool:
        return self.ui.portCombo.currentText().strip() == LOCAL_MODE_LABEL

    def _is_local_mode_active(self) -> bool:
        return self.local_mode_active and not self.is_connected

    def _normalize_port_name(self, text: str) -> str:
        candidate = text.strip()
        if candidate.lower().startswith("com") and candidate[3:].isdigit():
            return f"COM{candidate[3:]}"
        return candidate

    def _add_port_name(self, names: list[str], text: str) -> None:
        candidate = self._normalize_port_name(text)
        if candidate and candidate not in names:
            names.append(candidate)

    def _scan_serial_port_names(self) -> list[str]:
        names: list[str] = []
        try:
            for port in list_ports.comports():
                self._add_port_name(names, getattr(port, "device", ""))
        except Exception:  # noqa: BLE001
            pass
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DEVICEMAP\SERIALCOMM",
                ) as key:
                    index = 0
                    while True:
                        try:
                            _value_name, value_data, _value_type = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        self._add_port_name(names, str(value_data))
                        index += 1
            except Exception:  # noqa: BLE001
                pass
        return names

    def _remember_selected_port(self, text: str = "") -> None:
        self.manual_port_choice_made = True
        candidate = self._normalize_port_name(text or self.ui.portCombo.currentText())
        if not candidate or candidate == LOCAL_MODE_LABEL:
            self.preferred_port_name = ""
            return
        self.preferred_port_name = candidate

    def _install_wheel_guard(self, widget: QtCore.QObject) -> None:
        if widget.property("wheelGuardInstalled"):
            return
        widget.installEventFilter(self)
        widget.setProperty("wheelGuardInstalled", True)

    def _configure_port_combo(self) -> None:
        combo = self.ui.portCombo
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        combo.setDuplicatesEnabled(False)
        combo.setMinimumContentsLength(12)
        combo.setMinimumWidth(180)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        combo.setToolTip("选择扫描到的串口；列表暂时为空时可直接输入 COM5。")
        self._install_wheel_guard(combo)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("选择或输入 COM5")
            line_edit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            line_edit.editingFinished.connect(self._remember_selected_port)

    def _save_runtime_state(self) -> None:
        save_runtime_state(APP_DIR, self.runtime_state)

    def _normalize_mode_value(self, value: str | None, fallback: str = "DAY") -> str:
        candidate = (value or "").strip().upper()
        if candidate in {"DAY", "NIGHT"}:
            return candidate
        fallback_value = (fallback or "").strip().upper()
        return fallback_value if fallback_value in {"DAY", "NIGHT"} else "DAY"

    def _set_mode_state(
        self,
        value: str | None,
        *,
        save: bool = True,
        update_combo: bool = True,
        update_theme: bool = True,
    ) -> str:
        mode = self._normalize_mode_value(value, self.last_mode)
        self.last_mode = mode
        self.runtime_state.mode = mode
        if update_combo and hasattr(self.ui, "modeCombo"):
            self.ui.modeCombo.setCurrentText(mode)
        if hasattr(self, "status_mode"):
            self.status_mode.setText(mode)
        if save:
            self._save_runtime_state()
        if update_theme:
            self._refresh_theme_from_mode()
        return mode

    def _is_valid_mode_value(self, value: str | None) -> bool:
        return (value or "").strip().upper() in {"DAY", "NIGHT"}

    def _sanitize_runtime_state(self) -> None:
        if self.runtime_state.format not in {"LEFT", "RIGHT"}:
            self.runtime_state.format = "LEFT"
        self._set_mode_state(self.runtime_state.mode, save=False, update_combo=False, update_theme=False)

    def _set_runtime_datetime(self, moment: datetime) -> None:
        clean = moment.replace(microsecond=0)
        self.runtime_state.board_datetime_iso = clean.isoformat(sep=" ")
        self.runtime_shadow_base_iso = self.runtime_state.board_datetime_iso
        self.runtime_shadow_base_datetime = clean
        self.runtime_shadow_base_monotonic = time.monotonic()
        self.runtime_state.shadow_saved_at_utc_iso = datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(sep=" ")
        self._save_runtime_state()

    def _get_runtime_datetime(self) -> datetime:
        raw = self.runtime_state.board_datetime_iso.strip()
        if raw:
            try:
                board_time = datetime.fromisoformat(raw)
                if (
                    self.runtime_shadow_base_datetime is not None
                    and self.runtime_shadow_base_iso == raw
                ):
                    elapsed = max(
                        0,
                        int(time.monotonic() - self.runtime_shadow_base_monotonic),
                    )
                    return self.runtime_shadow_base_datetime + timedelta(seconds=elapsed)
                saved_raw = self.runtime_state.shadow_saved_at_utc_iso.strip()
                if saved_raw:
                    try:
                        saved_at = datetime.fromisoformat(saved_raw)
                    except ValueError:
                        return board_time
                    else:
                        if saved_at.tzinfo is None:
                            saved_at = saved_at.replace(tzinfo=timezone.utc)
                        else:
                            saved_at = saved_at.astimezone(timezone.utc)
                        elapsed = max(
                            0,
                            int((datetime.now(timezone.utc) - saved_at).total_seconds()),
                        )
                        current = board_time + timedelta(seconds=elapsed)
                        self.runtime_shadow_base_iso = raw
                        self.runtime_shadow_base_datetime = current
                        self.runtime_shadow_base_monotonic = time.monotonic()
                        return current
                self.runtime_shadow_base_iso = raw
                self.runtime_shadow_base_datetime = board_time
                self.runtime_shadow_base_monotonic = time.monotonic()
                return board_time
            except ValueError:
                pass
        fallback = self._selected_zone_now().replace(tzinfo=None, microsecond=0)
        self._set_runtime_datetime(fallback)
        return fallback

    def _oriented_text_to_frame(self, oriented: str) -> tuple[str, int]:
        chars: list[str] = []
        dp_mask = 0
        for ch in oriented:
            if ch == ".":
                if len(chars) < 8:
                    dp_mask |= 1 << len(chars)
                    chars.append(" ")
                continue
            if len(chars) >= 8:
                break
            chars.append(ch)
        while len(chars) < 8:
            chars.append(" ")
        token = "".join(chars[:8])
        return token, dp_mask & 0xFF

    def _visible_text_to_frame(self, visible: str) -> tuple[str, int]:
        oriented = visible[::-1] if self.runtime_state.format == "RIGHT" else visible
        return self._oriented_text_to_frame(oriented)

    def _set_local_display_override(
        self,
        visible_text: str,
        duration_s: float,
        led_mask: int | None = None,
    ) -> None:
        self.local_display_override_text = visible_text
        self.local_display_override_started = time.monotonic()
        self.local_display_override_until = time.monotonic() + duration_s
        self.local_display_override_led_mask = led_mask

    def _message_display_duration_s(self, text: str) -> float:
        visible_len = max(1, len(text.strip()))
        if visible_len <= 8:
            return 7.0
        return 10.0

    def _scroll_leg_count(self, limit: int) -> int:
        if limit <= 0:
            return 0
        if limit <= 5:
            return 3
        return 2

    def _scroll_max_step(self, limit: int) -> int:
        legs = self._scroll_leg_count(limit)
        if legs <= 0:
            return 0
        return legs * (limit + 1)

    def _scroll_offset_for_step(self, step: int, limit: int) -> int:
        if limit <= 0:
            return 0
        max_step = self._scroll_max_step(limit)
        step = min(max(0, step), max_step)
        if step == 0:
            return 0

        step -= 1
        leg = step // (limit + 1)
        in_leg = step % (limit + 1)
        legs = self._scroll_leg_count(limit)
        if leg >= legs:
            leg = max(0, legs - 1)
            in_leg = limit
        progress = in_leg + 1 if in_leg < limit else limit
        if leg % 2:
            return limit - progress
        return progress

    def _finite_text_frame(self, text: str, started_at: float, duration_s: float) -> tuple[str, int]:
        if len(text) <= 8:
            return self._visible_text_to_frame(text)
        elapsed = max(0.0, time.monotonic() - started_at)
        limit = max(0, len(text) - 8)
        duration = min(10.0, max(7.0, duration_s))
        scroll_duration = max(5.0, duration - 2.0)
        max_step = self._scroll_max_step(limit)
        interval = scroll_duration / max(1, max_step + 1)
        step = min(max_step, int(elapsed / max(0.1, interval)))
        offset = self._scroll_offset_for_step(step, limit)
        if self.runtime_state.format == "RIGHT":
            oriented = text[::-1]
            start = max(0, limit - offset)
        else:
            oriented = text
            start = offset
        return self._oriented_text_to_frame(oriented[start : start + 8])

    def _local_override_frame(self) -> tuple[str, int]:
        text = self.local_display_override_text
        duration = self.local_display_override_until - self.local_display_override_started
        return self._finite_text_frame(text, self.local_display_override_started, duration)

    def _weekday_english_name(self, moment: datetime) -> str:
        return [
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        ][moment.weekday()]

    def _weekday_chinese_name(self, moment: datetime) -> str:
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][moment.weekday()]

    def _local_view_frame(self, visible: str, view_mode: str) -> tuple[str, int]:
        if view_mode != "WEEKDAY" or len(visible) <= 8:
            return self._visible_text_to_frame(visible)
        key = f"{view_mode}:{visible}:{self.runtime_state.format}"
        if self.local_view_scroll_key != key:
            self.local_view_scroll_key = key
            self.local_view_scroll_started = time.monotonic()
        return self._finite_text_frame(
            visible,
            self.local_view_scroll_started,
            self._message_display_duration_s(visible),
        )

    def _current_local_display_frame(self) -> tuple[str, int]:
        if not self.runtime_state.display_on:
            return "        ", 0
        now_monotonic = time.monotonic()
        if self.local_display_override_until and now_monotonic > self.local_display_override_until:
            self.local_display_override_text = ""
            self.local_display_override_started = 0.0
            self.local_display_override_until = 0.0
            self.local_display_override_led_mask = None
        if self.local_display_override_text:
            return self._local_override_frame()
        board_time = self._get_runtime_datetime()
        view_mode = (getattr(self.runtime_state, "view_mode", "TIME") or "TIME").upper()
        if view_mode not in {"TIME", "DATE", "WEEKDAY", "YEAR"}:
            view_mode = "TIME"
        if view_mode == "DATE":
            visible = f"{board_time.year % 100:02d}.{board_time.month:02d}.{board_time.day:02d}"
        elif view_mode == "WEEKDAY":
            visible = self._weekday_english_name(board_time)
        elif view_mode == "YEAR":
            visible = f"{board_time.year:04d}.{board_time.month:02d}{board_time.day:02d}"
        elif self.runtime_state.mode == "NIGHT":
            visible = f"{board_time.hour:02d}.{board_time.minute:02d}"
        else:
            visible = (
                f"{board_time.hour:02d}."
                f"{board_time.minute:02d}."
                f"{board_time.second:02d}"
            )
        return self._local_view_frame(visible, view_mode)

    def _current_local_led_mask(self) -> int:
        if not self.runtime_state.display_on:
            return 0
        if (
            self.local_display_override_until
            and time.monotonic() <= self.local_display_override_until
            and self.local_display_override_led_mask is not None
        ):
            return self.local_display_override_led_mask & 0xFF
        if self.runtime_state.mode == "NIGHT":
            return 0x01
        if self.runtime_state.led_mask:
            return self.runtime_state.led_mask & 0xFF
        result = 0x01
        if self.runtime_state.alarm_enabled:
            result |= 0x02
        if self.runtime_state.format == "RIGHT":
            result |= 0x40
        return result & 0xFF

    def _refresh_local_twin_frame(self) -> None:
        if self.is_connected:
            return
        token, dp_mask = self._current_local_display_frame()
        led_mask = self._current_local_led_mask()
        self.twin.set_display_frame(token, dp_mask)
        self.twin.set_led_byte(led_mask)
        self.latest_display_text = f"{token} / {dp_mask:02X}"
        if hasattr(self, "latestDisplayLabel"):
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        self._update_latest_led_label(led_mask)

    def _start_boot_mirror_playback(self) -> None:
        self.boot_mirror_generation += 1
        generation = self.boot_mirror_generation
        frames = [
            (0, "88888888", 0xFF, 0xFF),
            (1000, "        ", 0x00, 0x00),
            (2000, "31910102", 0x00, 0xFF),
            (3000, "        ", 0x00, 0x00),
            (4000, "CHENYH  ", 0x00, 0xFF),
            (5000, "        ", 0x00, 0x00),
            (6000, "V2 0    ", 0x04, 0xFF),
        ]
        for delay_ms, token, dp_mask, led_mask in frames:
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda token=token, dp_mask=dp_mask, led_mask=led_mask, generation=generation: self._apply_boot_mirror_frame(
                    generation, token, dp_mask, led_mask
                ),
            )
        QtCore.QTimer.singleShot(
            7200,
            lambda generation=generation: self._finish_boot_mirror_playback(generation),
        )

    def _apply_boot_mirror_frame(
        self,
        generation: int,
        token: str,
        dp_mask: int,
        led_mask: int,
    ) -> None:
        if generation != self.boot_mirror_generation:
            return
        if self.is_connected:
            return
        self.twin.set_display_frame(token, dp_mask)
        self.twin.set_led_byte(led_mask)
        self.latest_display_text = f"{token} / {dp_mask:02X}"
        if hasattr(self, "latestDisplayLabel"):
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        self._update_latest_led_label(led_mask)

    def _finish_boot_mirror_playback(self, generation: int) -> None:
        if generation != self.boot_mirror_generation:
            return
        self._refresh_local_twin_frame()

    def _local_apply_notice(self, action: str) -> None:
        self.log("WARN", f"!!! 仅更新上位机本地配置/模拟状态，未下发板端：{action}")

    def _local_query_notice(self, action: str) -> None:
        self.log("WARN", f"!!! 当前为本地模式，显示的是上位机保存状态，并非板端实时返回：{action}")

    def _parse_alarm_time_text(self, text: str) -> QtCore.QTime:
        normalized = (text or "").strip().replace(".", ":")
        alarm_time = QtCore.QTime.fromString(normalized, "HH:mm:ss")
        if alarm_time.isValid():
            return alarm_time
        reversed_text = normalized[::-1]
        return QtCore.QTime.fromString(reversed_text, "HH:mm:ss")

    def _apply_alarm_state_from_text(self, text: str, source: str = "") -> None:
        value = (text or "").strip()
        upper = value.upper()
        if not value or upper == "OFF":
            self.runtime_state.alarm_enabled = False
            self.last_alarm = "OFF"
            self._save_runtime_state()
            self._refresh_single_alarm_ui()
            return
        if upper == "RINGING":
            self.last_alarm = "RINGING"
            self.runtime_state.alarm_enabled = True
            self._save_runtime_state()
            self._refresh_single_alarm_ui()
            return

        alarm_time = self._parse_alarm_time_text(value)
        if not alarm_time.isValid():
            if source:
                self.log("WARN", f"{source} 返回的闹钟时间无法解析: {value}")
            return
        self.ui.alarmTimeEdit.setTime(alarm_time)
        if hasattr(self, "scheduleAlarmTimeEdit"):
            self.scheduleAlarmTimeEdit.setTime(alarm_time)
        self.runtime_state.alarm_enabled = True
        self.runtime_state.alarm_time = alarm_time.toString("HH:mm:ss")
        self.last_alarm = self.runtime_state.alarm_time
        self._save_runtime_state()
        self._refresh_single_alarm_ui()

    def _schedule_single_alarm_query(self, reason: str, delay_ms: int = 420) -> None:
        if self._is_local_mode_active():
            self._refresh_single_alarm_ui()
            return
        self.single_alarm_query_generation += 1
        generation = self.single_alarm_query_generation
        QtCore.QTimer.singleShot(
            max(80, delay_ms),
            lambda generation=generation, reason=reason: self._run_single_alarm_query(
                generation, reason
            ),
        )

    def _run_single_alarm_query(self, generation: int, reason: str) -> None:
        if generation != self.single_alarm_query_generation:
            return
        if not self.is_connected:
            return
        if (
            self.sync_in_progress
            or self.ntp_fetch_in_progress
            or self.weather_refresh_in_progress
            or self.test_run_in_progress
            or self.pending_queries
            or self._serial_auto_quiet_active()
        ):
            delay_ms = self._serial_auto_quiet_delay_ms(220) if self._serial_auto_quiet_active() else 520
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda generation=generation, reason=reason: self._run_single_alarm_query(
                    generation, reason
                ),
            )
            return
        self.log("INFO", f"{reason}：自动查询板载单次闹钟状态。")
        self.send_command("*GET:ALARM", "ALARM")

    def _advance_local_display_view_from_disp_key(self) -> None:
        modes = ["TIME", "DATE", "WEEKDAY", "YEAR"]
        current = self.runtime_state.view_mode if self.runtime_state.view_mode in modes else "TIME"
        self.runtime_state.display_on = True
        self.runtime_state.view_mode = modes[(modes.index(current) + 1) % len(modes)]
        self.local_display_override_text = ""
        self.local_display_override_started = 0.0
        self.local_display_override_until = 0.0
        self.local_display_override_led_mask = None
        self.local_view_scroll_key = ""
        self.ui.displayToggleCombo.setCurrentText("ON")
        self._save_runtime_state()
        self._refresh_local_twin_frame()

    def _led_active_description(self, value: int) -> str:
        compact_names = {
            "串口RX": "RX",
            "串口TX": "TX",
            "手动覆盖": "手动",
        }
        active = [
            f"{label}{compact_names.get(name, name)}"
            for bit, (label, name) in enumerate(LED_BIT_LABELS)
            if value & (1 << bit)
        ]
        return "、".join(active) if active else "全灭"

    def _led_legend_text(self) -> str:
        return "LED 位义：" + "  ".join(
            f"{label}{name}" for label, name in LED_BIT_LABELS
        ) + "；天气短显时按天气掩码临时覆盖整组 LED"

    def _update_latest_led_label(self, value: int) -> None:
        value &= 0xFF
        self.latest_led_text = f"{value:02X}"
        if hasattr(self, "latestLedLabel"):
            self.latestLedLabel.setText(f"LED: {self.latest_led_text}")
            self.latestLedLabel.setToolTip(
                f"{self._led_active_description(value)}\n{self._led_legend_text()}"
            )
        if hasattr(self, "ledLegendLabel"):
            self.ledLegendLabel.setVisible(False)

    def _apply_runtime_state_to_ui(self) -> None:
        self.ui.displayToggleCombo.setCurrentText("ON" if self.runtime_state.display_on else "OFF")
        self.ui.formatCombo.setCurrentText(self.runtime_state.format)
        self._set_mode_state(self.runtime_state.mode, save=False, update_combo=True, update_theme=False)
        self.last_alarm = self.runtime_state.alarm_time if self.runtime_state.alarm_enabled else "OFF"
        alarm_time = QtCore.QTime.fromString(self.runtime_state.alarm_time, "HH:mm:ss")
        if self.runtime_state.alarm_enabled and alarm_time.isValid():
            self.ui.alarmTimeEdit.setTime(alarm_time)
            if hasattr(self, "scheduleAlarmTimeEdit"):
                self.scheduleAlarmTimeEdit.setTime(alarm_time)
        else:
            target = self._default_near_future_datetime()
            self.ui.alarmTimeEdit.setTime(QtCore.QTime(target.hour, target.minute, 0))
            if hasattr(self, "scheduleAlarmTimeEdit"):
                self.scheduleAlarmTimeEdit.setTime(QtCore.QTime(target.hour, target.minute, 0))
        board_time = self._get_runtime_datetime()
        self.ui.dateEdit.setDate(QtCore.QDate(board_time.year, board_time.month, board_time.day))
        self.ui.timeEdit.setTime(QtCore.QTime(board_time.hour, board_time.minute, board_time.second))
        self.ui.ledHexEdit.setText(f"{self.runtime_state.led_mask:02X}")
        self.ui.messageEdit.setText(self.runtime_state.message_text)
        self._refresh_local_twin_frame()
        self._refresh_theme_from_mode()
        self._refresh_single_alarm_ui()

    def _selected_zone_now(self, utc_moment: datetime | None = None) -> datetime:
        place = self._active_place()
        return timezone_now(
            place.timezone,
            utc_moment,
            fallback_offset_seconds=place.utc_offset_seconds,
        )

    def _default_near_future_datetime(self) -> datetime:
        return (
            self._selected_zone_now()
            .replace(tzinfo=None, second=0, microsecond=0)
            + timedelta(minutes=1)
        )

    def _apply_default_schedule_datetime(self) -> None:
        target = self._default_near_future_datetime()
        qtime = QtCore.QTime(target.hour, target.minute, 0)
        if hasattr(self, "scheduleTimeEdit"):
            self.scheduleTimeEdit.setTime(qtime)
        if hasattr(self, "scheduleAlarmTimeEdit"):
            self.scheduleAlarmTimeEdit.setTime(qtime)
        if hasattr(self, "scheduleDateEdit"):
            self.scheduleDateEdit.setDate(QtCore.QDate(target.year, target.month, target.day))

    def _current_place_label(self, place: SavedPlace) -> str:
        current_text = timezone_now(
            place.timezone,
            fallback_offset_seconds=place.utc_offset_seconds,
        ).strftime("%H:%M")
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
        self._refresh_network_time_label()

    def _active_place_time_context(self, utc_moment: datetime | None = None) -> tuple[SavedPlace, datetime, int]:
        place = self._active_place()
        zone_now = self._selected_zone_now(utc_moment)
        offset_seconds = int(zone_now.utcoffset().total_seconds()) if zone_now.utcoffset() else place.utc_offset_seconds
        return place, zone_now, offset_seconds

    def _refresh_network_time_label(self) -> None:
        place, zone_now, offset_seconds = self._active_place_time_context()
        self.last_selected_zone_time = zone_now.strftime("%Y-%m-%d %H:%M:%S")
        time_text = (
            f"当前时间: {self.last_selected_zone_time} | "
            f"时区: {place.timezone} ({format_utc_offset(offset_seconds)})"
        )
        if hasattr(self, "networkTimeLabel"):
            self.networkTimeLabel.setText(time_text)
        if hasattr(self, "sunriseSunsetLabel"):
            self.sunriseSunsetLabel.setText(
                f"日出/日落: {self.sunrise_text} / {self.sunset_text}"
            )

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

    def _format_dashboard_countdown(self, target: datetime | None, now: datetime) -> str:
        if target is None:
            return "无"
        delta_seconds = int((target - now).total_seconds())
        if delta_seconds <= 0 or delta_seconds < 60:
            return "<1 分钟"

        minutes_total = delta_seconds // 60
        days, remainder_minutes = divmod(minutes_total, 24 * 60)
        hours, minutes = divmod(remainder_minutes, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}天")
        if hours:
            parts.append(f"{hours}小时")
        if minutes:
            parts.append(f"{minutes}分钟")
        return "".join(parts) if parts else "<1 分钟"

    def _format_weather_age(self, now: datetime) -> str:
        if self.last_weather_refresh_at is None:
            return "未更新"
        age_minutes = max(
            0,
            int((datetime.now() - self.last_weather_refresh_at).total_seconds() // 60),
        )
        if age_minutes == 0:
            return "刚刚更新"
        if age_minutes == 1:
            return "1 分钟前更新"
        return f"{age_minutes} 分钟前更新"

    def _greeting_text(self, now: datetime) -> str:
        name = (self.config.user_name or "用户").strip() or "用户"
        if 5 <= now.hour < 11:
            return f"早上好，{name}！"
        if 11 <= now.hour < 14:
            return f"中午好，{name}！"
        if 14 <= now.hour < 18:
            return f"下午好，{name}！"
        if 18 <= now.hour < 22:
            return f"晚上好，{name}！"
        return f"夜深了，{name}，注意休息！"

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

    def _palette_for_colors(self, background: str, foreground: str) -> QtGui.QPalette:
        palette = QtGui.QPalette()
        bg = QtGui.QColor(background)
        fg = QtGui.QColor(foreground)
        for role in (
            QtGui.QPalette.Window,
            QtGui.QPalette.Base,
            QtGui.QPalette.AlternateBase,
            QtGui.QPalette.Button,
        ):
            palette.setColor(role, bg)
        for role in (
            QtGui.QPalette.WindowText,
            QtGui.QPalette.Text,
            QtGui.QPalette.ButtonText,
            QtGui.QPalette.ToolTipText,
        ):
            palette.setColor(role, fg)
        return palette

    def _asset_qss_url(self, name: str) -> str:
        return str(BUNDLE_DIR / "assets" / name).replace("\\", "/")

    def _apply_theme(self) -> None:
        night = self.config.theme_follow_mode and self.last_mode == "NIGHT"
        arrow_url = self._asset_qss_url(
            "combo_arrow_night.xpm" if night else "combo_arrow_day.xpm"
        )
        check_url = self._asset_qss_url("checkbox_check.xpm")
        spin_up_url = self._asset_qss_url(
            "spin_up_night.xpm" if night else "spin_up_day.xpm"
        )
        spin_down_url = self._asset_qss_url(
            "spin_down_night.xpm" if night else "spin_down_day.xpm"
        )
        palette = {
            "background": "#f4efe7",
            "text": "#16324f",
            "group_bg": "#fffdfa",
            "group_border": "#d7d0c6",
            "title": "#123b63",
            "button": "#1f5b8c",
            "button_hover": "#2b76b3",
            "button_pressed": "#174969",
            "button_disabled": "#8ba9c3",
            "input_bg": "#fcfaf7",
            "input_border": "#d7d0c6",
            "chip_bg": "#eef5fb",
            "chip_border": "#c5d7e8",
            "chip_text": "#234a70",
            "tab_bg": "#e5edf5",
            "status_bg": "#fffdfa",
            "scrollbar_bg": "#ebe4da",
            "scrollbar_handle": "#b7cadb",
            "scrollbar_handle_hover": "#96b5cf",
            "splitter_handle": "#d7d0c6",
            "splitter_handle_hover": "#b7cadb",
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
                    "button_disabled": "#405468",
                    "input_bg": "#1d2a38",
                    "input_border": "#445365",
                    "chip_bg": "#233345",
                    "chip_border": "#445365",
                    "chip_text": "#dbe7f2",
                    "tab_bg": "#2b3c4d",
                    "status_bg": "#151e27",
                    "scrollbar_bg": "#18212b",
                    "scrollbar_handle": "#516477",
                    "scrollbar_handle_hover": "#667b90",
                    "splitter_handle": "#445365",
                    "splitter_handle_hover": "#516477",
                }
            )
        base_palette = self._palette_for_colors(palette["background"], palette["text"])
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setPalette(base_palette)
        self.setPalette(base_palette)
        self.setAutoFillBackground(True)
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
            QWidget#centralwidget {{
                background: {palette['background']};
            }}
            QWidget#mainLeftPanel, QWidget#mainRightPanel {{
                background: {palette['background']};
            }}
            QWidget#twinContainer {{
                background: transparent;
            }}
            QGroupBox {{
                background: {palette['group_bg']};
                border: 1px solid {palette['group_border']};
                border-radius: 8px;
                margin-top: 24px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                top: 4px;
                padding: 0 8px 2px 8px;
                background: {palette['group_bg']};
                color: {palette['title']};
            }}
            QGroupBox#twinGroup {{
                background: {palette['group_bg']};
                border: 1px solid {palette['group_border']};
                border-radius: 8px;
                margin-top: 0px;
            }}
            QGroupBox#twinGroup::title, QGroupBox#logGroup::title {{
                height: 0px;
                padding: 0px;
                margin: 0px;
                color: transparent;
                background: transparent;
            }}
            QGroupBox#logGroup {{
                background: {palette['group_bg']};
                border: 1px solid {palette['group_border']};
                border-radius: 8px;
                margin-top: 0px;
            }}
            QPushButton, QToolButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 5px 10px;
                font-weight: 600;
                min-height: 30px;
                font-size: 12px;
            }}
            QPushButton:hover, QToolButton:hover {{
                background: {palette['button_hover']};
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background: {palette['button_pressed']};
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background: {palette['button_disabled']};
                color: rgba(255, 255, 255, 0.55);
            }}
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QTextEdit {{
                background: {palette['input_bg']};
                border: 1px solid {palette['input_border']};
                border-radius: 8px;
                padding: 6px 8px;
            }}
            QComboBox, QDateEdit, QTimeEdit {{
                padding-right: 34px;
            }}
            QComboBox::drop-down, QDateEdit::drop-down, QTimeEdit::drop-down {{
                background: {palette['input_bg']};
                border-left: 1px solid {palette['input_border']};
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 34px;
            }}
            QComboBox::down-arrow, QDateEdit::down-arrow, QTimeEdit::down-arrow {{
                image: url("{arrow_url}");
                width: 16px;
                height: 16px;
                margin-right: 9px;
                border: none;
                background: transparent;
            }}
            QComboBox QAbstractItemView {{
                background: {palette['input_bg']};
                color: {palette['text']};
                border: 1px solid {palette['input_border']};
                selection-background-color: {palette['button']};
                selection-color: white;
                outline: none;
            }}
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
                background: {palette['input_bg']};
                border-left: 1px solid {palette['input_border']};
                subcontrol-origin: border;
                width: 28px;
            }}
            QAbstractSpinBox::up-button {{
                border-top-right-radius: 8px;
            }}
            QAbstractSpinBox::down-button {{
                border-bottom-right-radius: 8px;
            }}
            QAbstractSpinBox::up-arrow {{
                image: url("{spin_up_url}");
                width: 10px;
                height: 10px;
            }}
            QAbstractSpinBox::down-arrow {{
                image: url("{spin_down_url}");
                width: 10px;
                height: 10px;
            }}
            QComboBox[stateField="true"] {{
                padding-right: 34px;
            }}
            QComboBox[stateField="true"]::drop-down {{
                background: {palette['input_bg']};
                border-left: 1px solid {palette['input_border']};
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 34px;
            }}
            QComboBox[stateField="true"]::down-arrow {{
                image: url("{arrow_url}");
                width: 16px;
                height: 16px;
                margin-right: 9px;
                border: none;
                background: transparent;
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
            QCalendarWidget QWidget {{
                background: {palette['input_bg']};
                color: {palette['text']};
            }}
            QCalendarWidget QToolButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 3px 6px;
            }}
            QCalendarWidget QAbstractItemView {{
                background: {palette['input_bg']};
                color: {palette['text']};
                selection-background-color: {palette['button']};
                selection-color: white;
            }}
            QScrollArea, QAbstractScrollArea {{
                border: none;
                background: {palette['background']};
            }}
            QScrollArea > QWidget > QWidget {{
                background: {palette['background']};
            }}
            QSplitter {{
                background: {palette['background']};
            }}
            QSplitter::handle {{
                background: {palette['splitter_handle']};
                border: none;
            }}
            QSplitter::handle:horizontal {{
                width: 6px;
                margin: 0px 2px;
            }}
            QSplitter::handle:vertical {{
                height: 6px;
                margin: 2px 0px;
            }}
            QSplitter::handle:hover {{
                background: {palette['splitter_handle_hover']};
            }}
            QScrollBar:vertical {{
                background: {palette['scrollbar_bg']};
                width: 12px;
                margin: 0px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {palette['scrollbar_handle']};
                min-height: 28px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {palette['scrollbar_handle_hover']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                background: transparent;
                border: none;
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                background: {palette['scrollbar_bg']};
                height: 12px;
                margin: 0px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {palette['scrollbar_handle']};
                min-width: 28px;
                border-radius: 6px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {palette['scrollbar_handle_hover']};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                background: transparent;
                border: none;
                width: 0px;
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            QLabel#twinTitle {{
                font-size: 9px;
                font-weight: 600;
                color: {palette['title']};
            }}
            QLabel#twinHeaderLabel {{
                font-size: 11px;
                font-weight: 700;
                color: {palette['title']};
            }}
            QToolTip {{
                background: {palette['chip_bg']};
                color: {palette['chip_text']};
                border: 1px solid {palette['chip_border']};
                border-radius: 6px;
                padding: 6px 8px;
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
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {palette['input_border']};
                border-radius: 3px;
                background: {palette['input_bg']};
            }}
            QCheckBox::indicator:checked {{
                image: url("{check_url}");
                background: {palette['button']};
                border: 1px solid {palette['button_hover']};
            }}
            QCheckBox::indicator:disabled {{
                background: {palette['button_disabled']};
                border: 1px solid {palette['group_border']};
            }}
            QCheckBox::indicator:checked:disabled {{
                image: url("{check_url}");
            }}
            QPushButton#twinKeyButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 2px 4px;
                min-height: 26px;
                font-size: 8px;
                font-weight: 700;
            }}
            QPushButton#twinKeyButton:hover {{
                background: {palette['button_hover']};
            }}
            QPushButton#twinKeyButton:pressed {{
                background: {palette['button_pressed']};
            }}
            QPushButton#accordionHeader {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 14px;
                min-height: 38px;
                font-weight: 600;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton#accordionHeader:hover {{
                background: {palette['button_hover']};
            }}
            QPushButton#accordionHeader:checked {{
                background: {palette['button_pressed']};
            }}
            QPushButton#accordionOptionButton {{
                background: {palette['button']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 14px;
                min-height: 36px;
                font-weight: 600;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton#accordionOptionButton:hover {{
                background: {palette['button_hover']};
            }}
            QPushButton#accordionOptionButton:pressed {{
                background: {palette['button_pressed']};
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
                background: {palette['status_bg']};
                color: {palette['text']};
                border-top: 1px solid {palette['group_border']};
            }}
            QStatusBar::item {{
                border-left: 1px solid {palette['group_border']};
                padding-left: 6px;
                padding-right: 6px;
            }}
            QStatusBar QLabel {{
                background: transparent;
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
            QToolButton:disabled {{
                background: {palette['button_disabled']};
                color: rgba(255, 255, 255, 0.55);
            }}
            """
        )
        self._apply_dynamic_theme_overrides(night, palette)

    def _apply_dynamic_theme_overrides(self, night: bool, palette: dict[str, str]) -> None:
        arrow_url = self._asset_qss_url(
            "combo_arrow_night.xpm" if night else "combo_arrow_day.xpm"
        )
        check_url = self._asset_qss_url("checkbox_check.xpm")
        spin_up_url = self._asset_qss_url(
            "spin_up_night.xpm" if night else "spin_up_day.xpm"
        )
        spin_down_url = self._asset_qss_url(
            "spin_down_night.xpm" if night else "spin_down_day.xpm"
        )
        scrollbar_style = (
            f"QScrollBar:vertical {{"
            f"background: {palette['scrollbar_bg']};"
            f"width: 12px;"
            f"margin: 0px;"
            f"border: none;"
            f"}}"
            f"QScrollBar::handle:vertical {{"
            f"background: {palette['scrollbar_handle']};"
            f"min-height: 28px;"
            f"border-radius: 6px;"
            f"}}"
            f"QScrollBar::handle:vertical:hover {{"
            f"background: {palette['scrollbar_handle_hover']};"
            f"}}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{"
            f"background: transparent;"
            f"border: none;"
            f"height: 0px;"
            f"}}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{"
            f"background: transparent;"
            f"}}"
            f"QScrollBar:horizontal {{"
            f"background: {palette['scrollbar_bg']};"
            f"height: 12px;"
            f"margin: 0px;"
            f"border: none;"
            f"}}"
            f"QScrollBar::handle:horizontal {{"
            f"background: {palette['scrollbar_handle']};"
            f"min-width: 28px;"
            f"border-radius: 6px;"
            f"}}"
            f"QScrollBar::handle:horizontal:hover {{"
            f"background: {palette['scrollbar_handle_hover']};"
            f"}}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{"
            f"background: transparent;"
            f"border: none;"
            f"width: 0px;"
            f"}}"
            f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{"
            f"background: transparent;"
            f"}}"
        )
        item_view_style = (
            f"QAbstractItemView, QListView, QListWidget, QTableWidget, QTableView {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"selection-background-color: {palette['button']};"
            f"selection-color: white;"
            f"outline: none;"
            f"}}"
            f"QAbstractItemView::item, QListWidget::item, QTableWidget::item {{"
            f"background: transparent;"
            f"color: {palette['text']};"
            f"}}"
            f"QAbstractItemView::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{"
            f"background: {palette['button']};"
            f"color: white;"
            f"}}"
            f"{scrollbar_style}"
        )
        item_view_viewport_style = (
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: none;"
        )
        text_edit_style = (
            f"QTextEdit {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 8px;"
            f"selection-background-color: {palette['button']};"
            f"selection-color: white;"
            f"}}"
            f"{scrollbar_style}"
        )
        field_style = (
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            "padding: 6px 10px;"
        )
        field_line_style = (
            f"background: transparent;"
            f"color: {palette['text']};"
            "border: none;"
            "padding: 0 4px;"
        )
        combo_style = (
            f"QComboBox {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 40px 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QComboBox::drop-down {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"border-top-right-radius: 8px;"
            f"border-bottom-right-radius: 8px;"
            f"subcontrol-origin: border;"
            f"subcontrol-position: top right;"
            f"width: 34px;"
            f"}}"
            f"QComboBox::down-arrow {{"
            f"image: url(\"{arrow_url}\");"
            f"width: 16px;"
            f"height: 16px;"
            f"margin-right: 9px;"
            f"border: none;"
            f"background: transparent;"
            f"}}"
        )
        state_combo_style = (
            f"QComboBox {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 40px 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QComboBox::drop-down {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"border-top-right-radius: 8px;"
            f"border-bottom-right-radius: 8px;"
            f"subcontrol-origin: border;"
            f"subcontrol-position: top right;"
            f"width: 34px;"
            f"}}"
            f"QComboBox::down-arrow {{"
            f"image: url(\"{arrow_url}\");"
            f"width: 16px;"
            f"height: 16px;"
            f"margin-right: 9px;"
            f"border: none;"
            f"background: transparent;"
            f"}}"
        )
        spin_style = (
            f"QDateEdit, QTimeEdit, QSpinBox {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 40px 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QDateEdit::drop-down, QTimeEdit::drop-down {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"border-top-right-radius: 8px;"
            f"border-bottom-right-radius: 8px;"
            f"subcontrol-origin: border;"
            f"subcontrol-position: top right;"
            f"width: 34px;"
            f"}}"
            f"QDateEdit::down-arrow, QTimeEdit::down-arrow {{"
            f"image: url(\"{arrow_url}\");"
            f"width: 16px;"
            f"height: 16px;"
            f"margin-right: 9px;"
            f"border: none;"
            f"background: transparent;"
            f"}}"
            f"QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"subcontrol-origin: border;"
            f"width: 28px;"
            f"}}"
            f"QAbstractSpinBox::up-button {{ border-top-right-radius: 8px; }}"
            f"QAbstractSpinBox::down-button {{ border-bottom-right-radius: 8px; }}"
            f"QAbstractSpinBox::up-arrow {{"
            f"image: url(\"{spin_up_url}\");"
            f"width: 10px;"
            f"height: 10px;"
            f"}}"
            f"QAbstractSpinBox::down-arrow {{"
            f"image: url(\"{spin_down_url}\");"
            f"width: 10px;"
            f"height: 10px;"
            f"}}"
        )
        header_style = (
            f"background: {palette['chip_bg']};"
            f"color: {palette['chip_text']};"
            f"border: 1px solid {palette['input_border']};"
        )
        page_style = f"background: {palette['background']}; color: {palette['text']};"
        viewport_style = f"background: {palette['background']}; color: {palette['text']};"
        scroll_page_style = (
            f"QScrollArea {{ background: {palette['background']}; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {palette['background']}; }}"
            f"{scrollbar_style}"
        )
        def make_button_style(selector: str) -> str:
            return (
                f"{selector} {{"
                f"background: {palette['button']};"
                f"color: white;"
                f"border: none;"
                f"border-radius: 8px;"
                f"padding: 5px 10px;"
                f"min-height: 28px;"
                f"font-weight: 700;"
                f"font-size: 12px;"
                f"text-align: center;"
                f"}}"
                f"{selector}:hover {{"
                f"background: {palette['button_hover']};"
                f"}}"
                f"{selector}:pressed {{"
                f"background: {palette['button_pressed']};"
                f"}}"
                f"{selector}:disabled {{"
                f"background: {palette['button_disabled']};"
                f"color: rgba(255,255,255,0.58);"
                f"}}"
            )
        card_style = (
            f"background: {palette['chip_bg']};"
            f"border: 1px solid {palette['chip_border']};"
            f"border-radius: 8px;"
        )
        card_title_style = (
            f"color: {palette['chip_text']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 10px;"
            f"font-weight: 700;"
        )
        card_value_style = (
            f"color: {palette['title']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 18px;"
            f"font-weight: 800;"
        )
        card_sub_style = (
            f"color: {palette['chip_text']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 10px;"
        )
        card_greeting_style = (
            f"color: {palette['title']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 17px;"
            f"font-weight: 800;"
        )
        embedded_title_style = (
            f"color: {palette['title']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 15px;"
            f"font-weight: 800;"
            f"padding: 0px;"
        )
        info_chip_style = (
            f"background: {palette['chip_bg']};"
            f"border: 1px solid {palette['chip_border']};"
            f"border-radius: 8px;"
            f"padding: 4px 8px;"
            f"color: {palette['chip_text']};"
            f"font-size: 11px;"
        )
        checkbox_style = (
            f"QCheckBox {{"
            f"background: transparent;"
            f"color: {palette['text']};"
            f"spacing: 6px;"
            f"font-size: 11px;"
            f"}}"
            f"QCheckBox::indicator {{"
            f"width: 14px;"
            f"height: 14px;"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 3px;"
            f"background: {palette['input_bg']};"
            f"}}"
            f"QCheckBox::indicator:checked {{"
            f"image: url(\"{check_url}\");"
            f"background: {palette['button']};"
            f"border: 1px solid {palette['button_hover']};"
            f"}}"
            f"QCheckBox::indicator:disabled {{"
            f"background: {palette['button_disabled']};"
            f"border: 1px solid {palette['group_border']};"
            f"}}"
            f"QCheckBox::indicator:checked:disabled {{"
            f"image: url(\"{check_url}\");"
            f"}}"
        )
        status_bar_style = (
            f"QStatusBar {{"
            f"background: {palette['status_bg']};"
            f"color: {palette['text']};"
            f"border-top: 1px solid {palette['group_border']};"
            f"}}"
            f"QStatusBar::item {{"
            f"border-left: 1px solid {palette['group_border']};"
            f"padding-left: 6px;"
            f"padding-right: 6px;"
            f"}}"
        )
        status_label_style = (
            f"background: transparent;"
            f"color: {palette['text']};"
            f"font-size: 12px;"
        )
        status_action_style = (
            f"QToolButton#statusActionButton {{"
            f"background: {palette['button']};"
            f"color: white;"
            f"border: none;"
            f"border-radius: 8px;"
            f"padding: 2px 8px;"
            f"min-height: 22px;"
            f"font-size: 11px;"
            f"font-weight: 700;"
            f"}}"
            f"QToolButton#statusActionButton:hover {{"
            f"background: {palette['button_hover']};"
            f"}}"
            f"QToolButton#statusActionButton:pressed {{"
            f"background: {palette['button_pressed']};"
            f"}}"
            f"QToolButton#statusActionButton:disabled {{"
            f"background: {palette['button_disabled']};"
            f"color: rgba(255,255,255,0.58);"
            f"}}"
        )
        background_palette = self._palette_for_colors(palette["background"], palette["text"])
        group_palette = self._palette_for_colors(palette["group_bg"], palette["text"])
        input_palette = self._palette_for_colors(palette["input_bg"], palette["text"])

        for widget in (
            getattr(self.ui, "centralwidget", None),
            getattr(self, "leftPanel", None),
            getattr(self, "rightScrollArea", None),
            getattr(self, "rightPanel", None),
        ):
            if widget is None:
                continue
            widget.setAutoFillBackground(True)
            widget.setPalette(background_palette)
        for widget in (
            getattr(self.ui, "twinGroup", None),
            getattr(self.ui, "logGroup", None),
        ):
            if widget is None:
                continue
            widget.setAutoFillBackground(True)
            widget.setPalette(group_palette)
        if hasattr(self.ui, "twinContainer"):
            self.ui.twinContainer.setAutoFillBackground(False)
            self.ui.twinContainer.setPalette(group_palette)
        for widget in (
            getattr(self.ui, "logTextEdit", None),
            getattr(self, "testOutputText", None),
        ):
            if widget is None:
                continue
            widget.setPalette(input_palette)

        for widget in self.findChildren(QtWidgets.QWidget, "sectionPageHost"):
            widget.setStyleSheet(page_style)
        for area in self.findChildren(QtWidgets.QScrollArea):
            area.setStyleSheet(scroll_page_style)
            area.viewport().setStyleSheet(page_style)
        for button_type in (QtWidgets.QPushButton, QtWidgets.QToolButton):
            for button in self.findChildren(button_type):
                if button.objectName() in {"accordionHeader", "accordionOptionButton", "twinKeyButton"}:
                    continue
                button.setStyleSheet(make_button_style(button_type.__name__))
                button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        for widget in self.findChildren(QtWidgets.QLineEdit):
            widget.setStyleSheet(field_style)
        for widget in self.findChildren(QtWidgets.QComboBox):
            widget.setStyleSheet(
                state_combo_style if widget.property("stateField") else combo_style
            )
            line_edit = widget.lineEdit()
            if line_edit is not None:
                line_edit.setStyleSheet(field_line_style)
                if widget.property("stateField"):
                    line_edit.setAlignment(QtCore.Qt.AlignCenter)
                    line_edit.setReadOnly(True)
                elif widget is getattr(self.ui, "portCombo", None):
                    line_edit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                else:
                    line_edit.setAlignment(QtCore.Qt.AlignCenter)
                    line_edit.setReadOnly(True)
            if widget.view() is not None:
                widget.view().setStyleSheet(item_view_style)
                widget.view().viewport().setStyleSheet(item_view_viewport_style)
        for widget_type in (QtWidgets.QDateEdit, QtWidgets.QTimeEdit, QtWidgets.QSpinBox):
            for widget in self.findChildren(widget_type):
                widget.setStyleSheet(spin_style)
                widget.setAlignment(QtCore.Qt.AlignCenter)
                line_edit = widget.findChild(QtWidgets.QLineEdit)
                if line_edit is not None:
                    line_edit.setAlignment(QtCore.Qt.AlignCenter)
                    line_edit.setStyleSheet(field_line_style)
        for calendar in self.findChildren(QtWidgets.QCalendarWidget):
            calendar.setStyleSheet(
                f"background: {palette['input_bg']};"
                f"color: {palette['text']};"
                f"border: 1px solid {palette['input_border']};"
                "border-radius: 8px;"
            )
        for widget in (
            getattr(self, "scheduleTable", None),
            getattr(self, "dashboardEventList", None),
            getattr(self, "testOutputText", None),
            getattr(self.ui, "logTextEdit", None),
        ):
            if widget is None:
                continue
            if isinstance(widget, QtWidgets.QTextEdit):
                widget.setStyleSheet(text_edit_style)
            else:
                widget.setStyleSheet(item_view_style)
            if hasattr(widget, "viewport") and widget.viewport() is not None:
                widget.viewport().setStyleSheet(item_view_viewport_style)
        for checkbox in self.findChildren(QtWidgets.QCheckBox):
            checkbox.setStyleSheet(checkbox_style)
        if hasattr(self, "scheduleTable"):
            header = self.scheduleTable.horizontalHeader()
            if header is not None:
                header.setStyleSheet(f"QHeaderView::section {{{header_style}}}")
            corner = self.scheduleTable.findChild(QtWidgets.QAbstractButton)
            if corner is not None:
                corner.setStyleSheet(header_style)
        if hasattr(self.ui, "statusbar"):
            self.ui.statusbar.setStyleSheet(status_bar_style)
            self.ui.statusbar.setAutoFillBackground(True)
            self.ui.statusbar.setPalette(self._palette_for_colors(palette["status_bg"], palette["text"]))
        for label in (
            getattr(self, "status_project", None),
            getattr(self, "status_features", None),
            getattr(self, "status_connection", None),
            getattr(self, "status_mode", None),
            getattr(self, "status_location", None),
            getattr(self, "status_local_time", None),
            getattr(self, "status_latency", None),
            getattr(self, "status_version", None),
            getattr(self, "status_developer", None),
        ):
            if label is not None:
                label.setStyleSheet(status_label_style)
        for button in (
            getattr(self, "status_clear_button", None),
            getattr(self, "status_export_button", None),
            getattr(self, "status_github_button", None),
        ):
            if button is not None:
                button.setStyleSheet(status_action_style)
        for card in self.findChildren(QtWidgets.QFrame):
            if card.property("dashboardCard"):
                card.setStyleSheet(card_style)
        for label in self.findChildren(QtWidgets.QLabel):
            if label.property("dashboardTitle"):
                label.setStyleSheet(card_title_style)
            elif label.property("dashboardValue"):
                label.setStyleSheet(card_value_style)
            elif label.property("dashboardSub"):
                label.setStyleSheet(card_sub_style)
            elif label.property("dashboardGreeting"):
                label.setStyleSheet(card_greeting_style)
            elif label.property("class") == "infoChip":
                label.setStyleSheet(info_chip_style)
            elif label.property("embeddedGroupTitle"):
                label.setStyleSheet(embedded_title_style)

    def _normalize_groupbox_layouts(self) -> None:
        for group in self.findChildren(QtWidgets.QGroupBox):
            if group is getattr(self.ui, "displayGroup", None):
                self._apply_display_group_layout_constraints()
                continue
            layout = group.layout()
            if layout is None:
                continue
            margins = layout.contentsMargins()
            top = margins.top()
            if group.title().strip() and top < 28:
                top = 28
            layout.setContentsMargins(
                margins.left(),
                top,
                margins.right(),
                margins.bottom(),
            )
            if isinstance(layout, QtWidgets.QGridLayout):
                layout.setHorizontalSpacing(max(layout.horizontalSpacing(), 12))
                layout.setVerticalSpacing(max(layout.verticalSpacing(), 12))
                for row in range(layout.rowCount()):
                    layout.setRowMinimumHeight(row, max(layout.rowMinimumHeight(row), 42))
                for col in range(layout.columnCount()):
                    if col == 0:
                        layout.setColumnMinimumWidth(
                            col,
                            max(layout.columnMinimumWidth(col), 82),
                        )
            elif isinstance(layout, QtWidgets.QVBoxLayout):
                layout.setSpacing(max(layout.spacing(), 10))

    def _make_settings_row(
        self,
        label_text: str,
        field: QtWidgets.QWidget,
        action: QtWidgets.QWidget | None = None,
        *,
        field_min_width: int = 150,
        action_width: int = 112,
    ) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(self.ui.displayGroup)
        row.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        row.setMinimumHeight(38)
        row.setMaximumHeight(40)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QtWidgets.QLabel(label_text, row)
        label.setMinimumWidth(82)
        label.setMaximumWidth(92)
        label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        layout.addWidget(label)

        field.setParent(row)
        field.setMinimumWidth(field_min_width)
        field.setMinimumHeight(34)
        field.setMaximumHeight(38)
        field.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(field, 1)

        if action is not None:
            action.setParent(row)
            action.setMinimumWidth(action_width)
            action.setMaximumWidth(max(action_width, 126))
            action.setMinimumHeight(34)
            action.setMaximumHeight(38)
            action.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            layout.addWidget(action)
        return row

    def _configure_centered_combo(self, combo: QtWidgets.QComboBox) -> None:
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setReadOnly(True)
            line_edit.setAlignment(QtCore.Qt.AlignCenter)
            line_edit.setClearButtonEnabled(False)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)

    def _configure_regular_combo(self, combo: QtWidgets.QComboBox) -> None:
        if combo is getattr(self.ui, "portCombo", None):
            return
        self._configure_centered_combo(combo)
        combo.setMinimumContentsLength(max(combo.minimumContentsLength(), 6))

    def _style_form_grid(
        self,
        layout: QtWidgets.QGridLayout,
        *,
        label_width: int = 90,
        row_height: int = 44,
        top_margin: int = 32,
        side_margin: int = 16,
        bottom_margin: int = 14,
    ) -> None:
        layout.setContentsMargins(side_margin, top_margin, side_margin, bottom_margin)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 1)
        if layout.columnCount() > 2:
            layout.setColumnStretch(2, 0)

        for row in range(layout.rowCount()):
            layout.setRowMinimumHeight(row, row_height)

        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            row, column, _row_span, _column_span = layout.getItemPosition(index)
            del row
            if column == 0 and isinstance(widget, QtWidgets.QLabel):
                widget.setMinimumWidth(label_width)
                widget.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            elif isinstance(
                widget,
                (
                    QtWidgets.QLineEdit,
                    QtWidgets.QComboBox,
                    QtWidgets.QDateEdit,
                    QtWidgets.QTimeEdit,
                    QtWidgets.QSpinBox,
                ),
            ):
                widget.setMinimumHeight(34)
                widget.setMaximumHeight(38)
                widget.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Fixed,
                )

    def _rebuild_system_settings_group(self) -> None:
        group = self.ui.displayGroup
        layout = self.ui.gridLayout_2
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in {
                self.ui.displayToggleCombo,
                self.ui.applyDisplayButton,
                self.ui.formatCombo,
                self.ui.applyFormatButton,
                self.ui.modeCombo,
                self.ui.applyModeButton,
                self.ui.messageEdit,
                self.ui.sendMessageButton,
                self.usernameEdit,
                self.usernameSaveButton,
                getattr(self, "factoryResetButton", None),
            }:
                widget.setParent(None)
                widget.deleteLater()
        layout.setContentsMargins(18, 32, 18, 18)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)
        for row in range(16):
            layout.setRowMinimumHeight(row, 0)
            layout.setRowStretch(row, 0)
        for column in range(6):
            layout.setColumnMinimumWidth(column, 0)
            layout.setColumnStretch(column, 0)
        layout.setColumnMinimumWidth(0, 82)
        layout.setColumnMinimumWidth(2, 136)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 0)

        self.ui.applyModeButton.setText("日夜切换")
        self.ui.sendMessageButton.setText("发送 MSG")
        self.usernameSaveButton.setText("确认用户名")
        if not hasattr(self, "factoryResetButton"):
            self.factoryResetButton = QtWidgets.QPushButton("恢复出厂设置", group)
            self.factoryResetButton.setObjectName("factoryResetButton")
        self.factoryResetButton.setText("恢复出厂设置")
        self.factoryResetButton.setParent(group)

        for combo in (self.ui.displayToggleCombo, self.ui.formatCombo, self.ui.modeCombo):
            self._configure_centered_combo(combo)
        for widget in (self.usernameEdit,):
            widget.setAlignment(QtCore.Qt.AlignCenter)
        self.ui.messageEdit.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        rows = [
            ("显示开关", self.ui.displayToggleCombo, self.ui.applyDisplayButton),
            ("FORMAT", self.ui.formatCombo, self.ui.applyFormatButton),
            ("MODE", self.ui.modeCombo, self.ui.applyModeButton),
            ("滚动消息", self.ui.messageEdit, self.ui.sendMessageButton),
            ("用户名", self.usernameEdit, self.usernameSaveButton),
        ]
        for row_index, (label_text, field, action) in enumerate(rows):
            label = QtWidgets.QLabel(label_text, group)
            label.setMinimumWidth(82)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            field.setParent(group)
            field.setMinimumWidth(138)
            field.setMinimumHeight(38)
            field.setMaximumHeight(42)
            field.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            action.setParent(group)
            action.setMinimumWidth(132)
            action.setMaximumWidth(152)
            action.setMinimumHeight(38)
            action.setMaximumHeight(42)
            action.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            layout.addWidget(label, row_index, 0)
            layout.addWidget(field, row_index, 1)
            layout.addWidget(action, row_index, 2)
            layout.setRowMinimumHeight(row_index, 50)
        reset_row = len(rows)
        reset_hint = QtWidgets.QLabel("恢复出厂设置会重置城市、主题、显示、闹钟、日程和本地运行状态；确认后会尽量同步默认状态到板端。", group)
        reset_hint.setWordWrap(True)
        reset_hint.setProperty("class", "infoChip")
        reset_hint.setStyleSheet("")
        layout.addWidget(reset_hint, reset_row, 0, 1, 2)
        layout.addWidget(self.factoryResetButton, reset_row, 2)
        layout.setRowMinimumHeight(reset_row, 54)
        self._apply_display_group_layout_constraints()

    def _apply_display_group_layout_constraints(self) -> None:
        if not hasattr(self, "usernameEdit") or not hasattr(self, "usernameSaveButton"):
            return
        group = self.ui.displayGroup
        layout = self.ui.gridLayout_2
        layout.setContentsMargins(18, 34, 18, 18)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)
        for column in range(6):
            layout.setColumnMinimumWidth(column, 0)
            layout.setColumnStretch(column, 0)
        layout.setColumnMinimumWidth(0, 82)
        layout.setColumnMinimumWidth(2, 136)
        layout.setColumnStretch(1, 1)
        for row in range(16):
            layout.setRowMinimumHeight(row, 0)
            layout.setRowStretch(row, 0)
        for row in range(6):
            layout.setRowMinimumHeight(row, 56)
        for widget in (
            self.ui.displayToggleCombo,
            self.ui.formatCombo,
            self.ui.modeCombo,
            self.ui.messageEdit,
            self.usernameEdit,
        ):
            widget.setMinimumWidth(138)
            widget.setMinimumHeight(42)
            widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for button in (
            self.ui.applyDisplayButton,
            self.ui.applyFormatButton,
            self.ui.applyModeButton,
            self.ui.sendMessageButton,
            self.usernameSaveButton,
            self.factoryResetButton,
        ):
            button.setMinimumWidth(132)
            button.setMaximumWidth(152)
            button.setMinimumHeight(40)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        group.setMinimumHeight(0)
        group.setMaximumHeight(456)
        group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def _refresh_theme_from_mode(self) -> None:
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
        self.ui.displayGroup.setMinimumHeight(0)
        self.ui.displayGroup.setMaximumHeight(456)
        self.ui.displayGroup.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
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
            self.latestDisplayLabel.setWordWrap(False)

            self.latestLedLabel = QtWidgets.QLabel("LED: --", summary_widget)
            self.latestLedLabel.setObjectName("latestLedLabel")
            self.latestLedLabel.setProperty("class", "infoChip")
            self.latestLedLabel.setStyleSheet("")
            self.latestLedLabel.setWordWrap(False)
            self.latestLedLabel.setToolTip(self._led_legend_text())

            self.latestEventLabel = QtWidgets.QLabel("最近事件: 等待数据", summary_widget)
            self.latestEventLabel.setObjectName("latestEventLabel")
            self.latestEventLabel.setProperty("class", "infoChip")
            self.latestEventLabel.setWordWrap(False)
            self.latestEventLabel.setStyleSheet("")

            self.ledLegendLabel = QtWidgets.QLabel(self._led_legend_text(), summary_widget)
            self.ledLegendLabel.setObjectName("ledLegendLabel")
            self.ledLegendLabel.setProperty("class", "infoChip")
            self.ledLegendLabel.setWordWrap(True)
            self.ledLegendLabel.setStyleSheet("")
            self.ledLegendLabel.setVisible(False)

            self.showHeartbeatCheck = QtWidgets.QCheckBox("显示心跳日志", summary_widget)
            self.showHeartbeatCheck.setChecked(False)
            self.autoScrollCheck = QtWidgets.QCheckBox("日志自动滚动", summary_widget)
            self.autoScrollCheck.setChecked(True)
            for chip in (
                self.latestDisplayLabel,
                self.latestLedLabel,
                self.latestEventLabel,
            ):
                chip.setMinimumHeight(34)
                chip.setMaximumHeight(42)
                chip.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Fixed,
                )
            summary_widget.setMaximumHeight(90)

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

        self.ui.messageEdit.setClearButtonEnabled(False)
        self.ui.rawCommandEdit.setClearButtonEnabled(False)
        self.ui.ledHexEdit.setClearButtonEnabled(False)
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
        self._apply_display_group_layout_constraints()
        self.ui.verticalLayout_2.setSpacing(10)
        self.ui.verticalLayout_2.setContentsMargins(14, 10, 14, 12)
        self.ui.verticalLayout_3.setSpacing(8)
        self.ui.verticalLayout_3.setContentsMargins(14, 10, 14, 12)
        self.ui.verticalLayout_4.setContentsMargins(14, 6, 14, 10)
        self.ui.verticalLayout_5.setContentsMargins(14, 10, 14, 12)

        self.ui.connectButton.setText("连接")
        self.ui.syncNowButton.setText("NTP 对时并写入 S800")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("日夜切换")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送当前指令")
        self.ui.abbrevDemoButton.setText("缩写当前指令")
        self.ui.mixedCaseDemoButton.setText("随机混合大小写")
        self.ui.portHintLabel.setText("115200 8N1，自动扫描 COM，显示延迟和事件。")

    def _refine_layout(self) -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1440, 900)
        target_width = min(1500, max(920, int(available.width() * 0.94)))
        target_height = min(900, max(600, int(available.height() * 0.92)))
        self.resize(target_width, target_height)
        self.setMinimumSize(900, 600)
        self.ui.horizontalLayout.setContentsMargins(10, 10, 10, 10)
        self.ui.horizontalLayout.setSpacing(10)

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
        left_panel.setObjectName("mainLeftPanel")
        self.leftPanel = left_panel
        left_panel.setMinimumWidth(360)
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.leftSections = CollapsibleNavWidget(left_panel)
        self.leftSections.add_section("主页", self._build_home_page())
        self.leftSections.add_section("系统设置", self._build_extension_settings_page())
        self.leftSections.add_section("闹钟与日程管理", self._build_alarm_schedule_page())
        self.leftSections.add_section("调试与测试", self._build_debug_test_page())
        left_layout.addWidget(self.leftSections)

        right_panel = QtWidgets.QWidget(self.ui.centralwidget)
        right_panel.setObjectName("mainRightPanel")
        self.rightPanel = right_panel
        self.rightScrollArea = None
        right_width = self._right_panel_width_for(target_width)
        right_panel.setMinimumWidth(right_width)
        right_panel.setMaximumWidth(right_width)
        right_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Expanding,
        )
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(self.ui.twinGroup, 0)
        right_layout.addWidget(self.ui.logGroup, 1)

        self.ui.connectionGroup.setMinimumHeight(124)
        self.ui.clockGroup.setMinimumHeight(178)
        self.ui.displayGroup.setMinimumHeight(0)
        self.ui.displayGroup.setMaximumHeight(456)
        self.ui.displayGroup.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.ui.demoGroup.setMinimumHeight(220)

        available_height = max(target_height, available.height())
        min_log_height = 120 if available_height >= 760 else 90
        self.twin.ensurePolished()
        twin_content_height = max(
            self.twin.sizeHint().height(),
            self.twin.minimumSizeHint().height(),
        )
        required_twin_height = max(twin_content_height + 18, 190)
        self.ui.twinGroup.setTitle("")
        self.twin.setMinimumHeight(twin_content_height)
        self.ui.twinGroup.setMinimumHeight(required_twin_height)
        self.ui.twinGroup.setMaximumHeight(required_twin_height + 4)
        self.ui.twinGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        self.ui.logGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self.ui.logGroup.setTitle("")
        self.ui.logGroup.setMinimumHeight(min_log_height)

        self.mainSplitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self.ui.centralwidget)
        self.mainSplitter.setChildrenCollapsible(False)
        self.mainSplitter.setHandleWidth(6)
        self.mainSplitter.addWidget(left_panel)
        self.mainSplitter.addWidget(right_panel)
        self.mainSplitter.setStretchFactor(0, 1)
        self.mainSplitter.setStretchFactor(1, 0)
        right_size = right_width
        left_size = max(360, target_width - right_size - 20)
        self.mainSplitter.setSizes([left_size, right_size])
        self.ui.horizontalLayout.addWidget(self.mainSplitter)
        QtCore.QTimer.singleShot(0, self._enforce_main_splitter_layout)

        log_layout = self.ui.logGroup.layout()
        if log_layout is not None and not hasattr(self, "latestDisplayLabel"):
            log_layout.setContentsMargins(14, 12, 14, 12)
            log_layout.setSpacing(8)

            self.logEmbeddedTitleLabel = QtWidgets.QLabel("日志与异常", self.ui.logGroup)
            self.logEmbeddedTitleLabel.setProperty("embeddedGroupTitle", True)
            self.logEmbeddedTitleLabel.setStyleSheet("")
            log_layout.insertWidget(0, self.logEmbeddedTitleLabel)

            summary_widget = QtWidgets.QWidget(self.ui.logGroup)
            self.logSummaryWidget = summary_widget
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
            self.latestDisplayLabel.setWordWrap(False)

            self.latestLedLabel = QtWidgets.QLabel("LED: --", summary_widget)
            self.latestLedLabel.setObjectName("latestLedLabel")
            self.latestLedLabel.setProperty("class", "infoChip")
            self.latestLedLabel.setStyleSheet("")
            self.latestLedLabel.setWordWrap(False)
            self.latestLedLabel.setToolTip(self._led_legend_text())

            self.latestEventLabel = QtWidgets.QLabel("最近事件: 等待数据", summary_widget)
            self.latestEventLabel.setObjectName("latestEventLabel")
            self.latestEventLabel.setProperty("class", "infoChip")
            self.latestEventLabel.setWordWrap(False)
            self.latestEventLabel.setStyleSheet("")

            self.ledLegendLabel = QtWidgets.QLabel(self._led_legend_text(), summary_widget)
            self.ledLegendLabel.setObjectName("ledLegendLabel")
            self.ledLegendLabel.setProperty("class", "infoChip")
            self.ledLegendLabel.setWordWrap(True)
            self.ledLegendLabel.setStyleSheet("")
            self.ledLegendLabel.setVisible(False)

            self.showHeartbeatCheck = QtWidgets.QCheckBox("心跳日志", summary_widget)
            self.showHeartbeatCheck.setChecked(False)
            self.autoScrollCheck = QtWidgets.QCheckBox("自动滚动", summary_widget)
            self.autoScrollCheck.setChecked(True)
            self.autoModeNoticeLabel = QtWidgets.QLabel("", summary_widget)
            self.autoModeNoticeLabel.setProperty("class", "infoChip")
            self.autoModeNoticeLabel.setStyleSheet("")
            self.autoModeNoticeLabel.setWordWrap(True)
            self.autoModeNoticeLabel.setVisible(False)
            for chip in (
                self.latestDisplayLabel,
                self.latestLedLabel,
                self.latestEventLabel,
            ):
                chip.setMinimumHeight(34)
                chip.setMaximumHeight(42)
                chip.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Fixed,
                )
            summary_widget.setMaximumHeight(90)

            summary_layout.addWidget(self.latestDisplayLabel, 0, 0)
            summary_layout.addWidget(self.latestLedLabel, 0, 1)
            summary_layout.addWidget(self.showHeartbeatCheck, 0, 2)
            summary_layout.addWidget(self.autoScrollCheck, 0, 3)
            summary_layout.addWidget(self.latestEventLabel, 1, 0, 1, 4)
            summary_layout.addWidget(self.autoModeNoticeLabel, 2, 0, 1, 4)
            log_layout.insertWidget(1, summary_widget)

        if self.ui.horizontalLayout_2.indexOf(self.ui.connectButton) == -1:
            self.ui.horizontalLayout_2.removeWidget(self.ui.refreshPortsButton)
            self.ui.refreshPortsButton.hide()
            self.ui.portHintLabel.hide()
            self.ui.horizontalLayout_2.addWidget(self.ui.connectButton)
            self.ui.horizontalLayout_2.addWidget(self.ui.disconnectButton)
            self.ui.verticalLayout_2.removeItem(self.ui.horizontalLayout_3)
            self.ui.verticalLayout_2.removeWidget(self.ui.portHintLabel)
        if not hasattr(self, "serialStatusLabel"):
            self.serialStatusLabel = QtWidgets.QLabel("串口状态：未连接", self.ui.connectionGroup)
            self.serialStatusLabel.setProperty("class", "infoChip")
            self.serialStatusLabel.setStyleSheet("")
            self.serialStatusLabel.setWordWrap(True)
            self.ui.verticalLayout_2.addWidget(self.serialStatusLabel)
        self.ui.horizontalLayout_2.setStretch(0, 6)
        self.ui.horizontalLayout_2.setStretch(1, 3)
        self.ui.horizontalLayout_2.setStretch(2, 3)
        self.ui.logTextEdit.setMinimumHeight(86 if available_height >= 760 else 72)
        self.ui.logTextEdit.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self.ui.logTextEdit.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.ui.logTextEdit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ui.logTextEdit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ui.logTextEdit.document().setMaximumBlockCount(self.max_log_blocks)
        self.ui.clearLogButton.setMinimumHeight(28)
        self.ui.exportLogButton.setMinimumHeight(28)
        self.ui.horizontalLayout_6.setSpacing(10)
        self.ui.horizontalLayout_6.setStretch(0, 1)
        self.ui.horizontalLayout_6.setStretch(1, 1)
        self.ui.logGroup.setMaximumHeight(16777215)

        for button in self.findChildren(QtWidgets.QPushButton):
            button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            if button.objectName() == "twinKeyButton":
                button.setMinimumHeight(28)
                button.setMaximumHeight(32)
                continue
            button.setMinimumHeight(34)
            button.setMinimumWidth(max(button.minimumWidth(), 76))
        for button in getattr(self.twin, "key_buttons", []):
            button.setMinimumWidth(0)

        for combo in self.findChildren(QtWidgets.QComboBox):
            self._install_wheel_guard(combo)
            self._configure_regular_combo(combo)
            combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(max(combo.minimumContentsLength(), 6))
        self.ui.portCombo.setMinimumContentsLength(12)
        self.ui.portCombo.setMinimumWidth(180)
        self.ui.portCombo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for spinbox in self.findChildren(QtWidgets.QAbstractSpinBox):
            self._install_wheel_guard(spinbox)

        self.ui.messageEdit.setClearButtonEnabled(False)
        self.ui.rawCommandEdit.setClearButtonEnabled(False)
        self.ui.ledHexEdit.setClearButtonEnabled(False)
        for spinbox in (self.ui.dateEdit, self.ui.timeEdit, self.ui.alarmTimeEdit):
            line_edit = spinbox.findChild(QtWidgets.QLineEdit)
            if line_edit is not None:
                line_edit.setClearButtonEnabled(False)

        self.ui.gridLayout.setColumnMinimumWidth(0, 54)
        self.ui.gridLayout.setColumnMinimumWidth(2, 70)
        self.ui.gridLayout.setColumnMinimumWidth(3, 64)
        self.ui.gridLayout.setColumnStretch(0, 0)
        self.ui.gridLayout.setColumnStretch(1, 1)
        self.ui.gridLayout.setColumnStretch(2, 0)
        self.ui.gridLayout.setColumnStretch(3, 0)
        self.ui.gridLayout.setHorizontalSpacing(8)
        self.ui.gridLayout.setVerticalSpacing(8)
        self.ui.gridLayout.setContentsMargins(16, 30, 16, 12)
        self.ui.gridLayout.setRowMinimumHeight(0, 24)
        self.ui.gridLayout.setRowMinimumHeight(1, 44)
        self.ui.gridLayout.setRowMinimumHeight(2, 44)
        self.ui.gridLayout.setRowMinimumHeight(3, 24)
        self.ui.gridLayout.setRowMinimumHeight(4, 42)
        self._apply_display_group_layout_constraints()
        self.ui.verticalLayout_2.setSpacing(10)
        self.ui.verticalLayout_2.setContentsMargins(12, 28, 12, 12)
        self.ui.verticalLayout_3.setSpacing(8)
        self.ui.verticalLayout_3.setContentsMargins(12, 28, 12, 12)
        self.ui.verticalLayout_4.setSpacing(4)
        self.ui.verticalLayout_4.setContentsMargins(12, 2, 12, 6)
        self.ui.verticalLayout_5.setSpacing(8)
        self.ui.verticalLayout_5.setContentsMargins(12, 20, 12, 8)

        self.ui.connectButton.setText("连接")
        self.ui.syncNowButton.setText("NTP 对时并写入 S800")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("日夜切换")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送当前指令")
        self.ui.abbrevDemoButton.setText("缩写当前指令")
        self.ui.mixedCaseDemoButton.setText("随机混合大小写")
        self._normalize_groupbox_layouts()
        self._compact_sync_clock_layout()
        self._configure_protocol_test_group()

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
        self.ui.clockGroup.setTitle("时间写入与 NTP 对时")
        for widget in (
            self.ui.alarmLabel,
            self.ui.alarmTimeEdit,
            self.ui.applyAlarmButton,
            self.ui.disableAlarmButton,
            self.ui.queryAlarmButton,
        ):
            widget.setVisible(False)
        layout = self.ui.gridLayout

        if not hasattr(self, "manualTimeWriteHintLabel"):
            self.manualTimeWriteHintLabel = QtWidgets.QLabel(
                "手动编辑写入：上方日期/时间可自行修改，点击“写入”发送到 S800。",
                self.ui.clockGroup,
            )
            self.manualTimeWriteHintLabel.setProperty("class", "infoChip")
            self.manualTimeWriteHintLabel.setWordWrap(True)
            self.manualTimeWriteHintLabel.setStyleSheet("")
        if not hasattr(self, "ntpTimeWriteHintLabel"):
            self.ntpTimeWriteHintLabel = QtWidgets.QLabel(
                "NTP 自动对时：从网络获取当前城市时间，再写入 S800。",
                self.ui.clockGroup,
            )
            self.ntpTimeWriteHintLabel.setProperty("class", "infoChip")
            self.ntpTimeWriteHintLabel.setWordWrap(True)
            self.ntpTimeWriteHintLabel.setStyleSheet("")

        layout.removeWidget(self.manualTimeWriteHintLabel)
        layout.removeWidget(self.ntpTimeWriteHintLabel)
        layout.removeWidget(self.ui.dateLabel)
        layout.removeWidget(self.ui.dateEdit)
        layout.removeWidget(self.ui.applyDateButton)
        layout.removeWidget(self.ui.queryDateButton)
        layout.removeWidget(self.ui.timeLabel)
        layout.removeWidget(self.ui.timeEdit)
        layout.removeWidget(self.ui.applyTimeButton)
        layout.removeWidget(self.ui.queryTimeButton)
        layout.removeWidget(self.ui.syncNowButton)

        layout.addWidget(self.manualTimeWriteHintLabel, 0, 0, 1, 4)
        layout.addWidget(self.ui.dateLabel, 1, 0, 1, 1)
        layout.addWidget(self.ui.dateEdit, 1, 1, 1, 1)
        layout.addWidget(self.ui.applyDateButton, 1, 2, 1, 1)
        layout.addWidget(self.ui.queryDateButton, 1, 3, 1, 1)
        layout.addWidget(self.ui.timeLabel, 2, 0, 1, 1)
        layout.addWidget(self.ui.timeEdit, 2, 1, 1, 1)
        layout.addWidget(self.ui.applyTimeButton, 2, 2, 1, 1)
        layout.addWidget(self.ui.queryTimeButton, 2, 3, 1, 1)
        layout.addWidget(self.ntpTimeWriteHintLabel, 3, 0, 1, 4)
        layout.addWidget(self.ui.syncNowButton, 4, 0, 1, 4)

        for editor in (self.ui.dateEdit, self.ui.timeEdit):
            editor.setMinimumWidth(0)
            editor.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored,
                QtWidgets.QSizePolicy.Fixed,
            )
        for button in (
            self.ui.applyDateButton,
            self.ui.queryDateButton,
            self.ui.applyTimeButton,
            self.ui.queryTimeButton,
        ):
            button.setMinimumWidth(64)
            button.setMaximumWidth(78)
            button.setMinimumHeight(34)
            button.setMaximumHeight(38)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Fixed,
                QtWidgets.QSizePolicy.Fixed,
            )

        self.ui.syncNowButton.setText("NTP 对时并写入 S800")
        self.ui.syncNowButton.setMinimumHeight(38)
        self.ui.clockGroup.setMinimumHeight(220)
        self._compact_sync_clock_layout()

    def _compact_sync_clock_layout(self) -> None:
        if not hasattr(self.ui, "gridLayout"):
            return
        layout = self.ui.gridLayout
        layout.setContentsMargins(16, 28, 16, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.setColumnMinimumWidth(0, 54)
        layout.setColumnMinimumWidth(2, 70)
        layout.setColumnMinimumWidth(3, 64)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 0)
        layout.setColumnStretch(3, 0)
        for row, height in {
            0: 28,
            1: 42,
            2: 42,
            3: 28,
            4: 40,
        }.items():
            layout.setRowMinimumHeight(row, height)
        self.ui.syncNowButton.setMaximumHeight(44)

    def _build_home_page(self) -> QtWidgets.QWidget:
        host = QtWidgets.QWidget(self.ui.centralwidget)
        host.setObjectName("sectionPageHost")
        host.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        outer = QtWidgets.QVBoxLayout(host)
        outer.setContentsMargins(6, 8, 6, 8)
        outer.setSpacing(10)
        outer.addWidget(self.ui.connectionGroup)
        outer.addWidget(self._build_dashboard_group(host), 1)
        return host

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
        self.scheduleAlarmTimeEdit.setAlignment(QtCore.Qt.AlignCenter)
        self.scheduleApplyAlarmButton = QtWidgets.QPushButton("启用", group)
        self.scheduleDisableAlarmButton = QtWidgets.QPushButton("关闭", group)
        self.scheduleDisableAlarmButton.setVisible(False)
        self.scheduleQueryAlarmButton = QtWidgets.QPushButton("查询", group)
        self.scheduleQueryAlarmButton.setMaximumWidth(96)
        self.alarmVoiceEnabledCheck = QtWidgets.QCheckBox("启用语音播报", group)
        self.voiceEnabledCheck = self.alarmVoiceEnabledCheck
        self.scheduleAlarmStatusLabel = QtWidgets.QLabel(
            "当前状态：关    触发时间：--",
            group,
        )
        self.scheduleAlarmStatusLabel.setProperty("class", "infoChip")
        self.scheduleAlarmStatusLabel.setStyleSheet("")
        self.scheduleAlarmHintLabel = QtWidgets.QLabel(
            "离线场景可用板载单次闹钟；复杂提醒建议使用下方日程管理。",
            group,
        )
        self.scheduleAlarmHintLabel.setProperty("class", "infoChip")
        self.scheduleAlarmHintLabel.setWordWrap(True)
        self.scheduleAlarmHintLabel.setStyleSheet("")

        button_row = QtWidgets.QWidget(group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleQueryAlarmButton)
        button_layout.addWidget(self.scheduleApplyAlarmButton)

        layout.addWidget(QtWidgets.QLabel("触发时间"), 0, 0)
        layout.addWidget(self.scheduleAlarmTimeEdit, 0, 1, 1, 2)
        layout.addWidget(self.scheduleAlarmStatusLabel, 1, 0, 1, 3)
        layout.addWidget(self.alarmVoiceEnabledCheck, 2, 1, 1, 2)
        layout.addWidget(button_row, 3, 1, 1, 2)
        layout.addWidget(self.scheduleAlarmHintLabel, 4, 0, 1, 3)
        self._style_form_grid(layout, label_width=92, row_height=42)
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
        self.scheduleBoardLabelEdit.setMaxLength(32)
        self.scheduleBoardLabelEdit.setPlaceholderText("最多 32 字，超 8 位滚动")
        self.scheduleTimeEdit = QtWidgets.QTimeEdit(schedule_group)
        self.scheduleTimeEdit.setDisplayFormat("HH:mm:ss")
        default_target = self._default_near_future_datetime()
        self.scheduleTimeEdit.setTime(QtCore.QTime(default_target.hour, default_target.minute, 0))
        self.scheduleTypeCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleTypeCombo.addItems(["单次执行", "每周重复"])
        self.scheduleDateEdit = QtWidgets.QDateEdit(schedule_group)
        self.scheduleDateEdit.setCalendarPopup(True)
        self.scheduleDateEdit.setDate(QtCore.QDate(default_target.year, default_target.month, default_target.day))
        self.scheduleRingCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleRingCombo.addItems([label for _, label in self.ring_names])
        self.scheduleRingPreviewButton = QtWidgets.QPushButton("预览", schedule_group)
        self.scheduleVoiceEnabledCheck = QtWidgets.QCheckBox("启用语音播报", schedule_group)
        self.scheduleVoiceEdit = QtWidgets.QLineEdit(schedule_group)
        self.scheduleVoiceEdit.setPlaceholderText("语音播报文案，留空则不播报")
        self.quietNightCheck = QtWidgets.QCheckBox("夜间抑制日程铃声", schedule_group)
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
        self.scheduleDeleteButton = QtWidgets.QPushButton("删除选中提醒", schedule_group)
        self.scheduleDateLabel = QtWidgets.QLabel("执行日期", schedule_group)
        self.scheduleWeekdayLabel = QtWidgets.QLabel("每周", schedule_group)
        self.scheduleWeekdayHost = weekday_host
        ring_row = QtWidgets.QWidget(schedule_group)
        ring_layout = QtWidgets.QHBoxLayout(ring_row)
        ring_layout.setContentsMargins(0, 0, 0, 0)
        ring_layout.setSpacing(8)
        ring_layout.addWidget(self.scheduleRingCombo, 1)
        ring_layout.addWidget(self.scheduleRingPreviewButton)

        form.addWidget(QtWidgets.QLabel("标题"), 0, 0)
        form.addWidget(self.scheduleTitleEdit, 0, 1)
        form.addWidget(QtWidgets.QLabel("板端标签"), 1, 0)
        form.addWidget(self.scheduleBoardLabelEdit, 1, 1)
        form.addWidget(QtWidgets.QLabel("时间"), 2, 0)
        form.addWidget(self.scheduleTimeEdit, 2, 1)
        form.addWidget(QtWidgets.QLabel("规则"), 3, 0)
        form.addWidget(self.scheduleTypeCombo, 3, 1)
        form.addWidget(self.scheduleDateLabel, 4, 0)
        form.addWidget(self.scheduleDateEdit, 4, 1)
        form.addWidget(self.scheduleWeekdayLabel, 5, 0)
        form.addWidget(weekday_host, 5, 1)
        form.addWidget(QtWidgets.QLabel("铃声"), 6, 0)
        form.addWidget(ring_row, 6, 1)
        form.addWidget(self.quietNightCheck, 7, 1)
        form.addWidget(QtWidgets.QLabel("语音"), 8, 0)
        form.addWidget(self.scheduleVoiceEnabledCheck, 8, 1)
        form.addWidget(self.scheduleVoiceEdit, 9, 1)

        button_row = QtWidgets.QWidget(schedule_group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleSaveButton)

        form.addWidget(button_row, 10, 1)
        form.addWidget(self.scheduleDeleteButton, 11, 1)
        schedule_layout.addLayout(form)
        self._style_form_grid(form, label_width=92, row_height=42)
        return schedule_group

    def _build_dashboard_card(
        self,
        parent: QtWidgets.QWidget,
        title: str,
        value: str,
        subtext: str,
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel, QtWidgets.QLabel]:
        card = QtWidgets.QFrame(parent)
        card.setProperty("dashboardCard", True)
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(2)

        title_label = QtWidgets.QLabel(title, card)
        title_label.setProperty("dashboardTitle", True)
        value_label = QtWidgets.QLabel(value, card)
        value_label.setProperty("dashboardValue", True)
        sub_label = QtWidgets.QLabel(subtext, card)
        sub_label.setProperty("dashboardSub", True)
        for label in (title_label, value_label, sub_label):
            label.setWordWrap(True)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setStyleSheet("")
        value_label.setMinimumHeight(24)

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(sub_label)
        layout.addStretch(1)
        return card, value_label, sub_label

    def _build_dashboard_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        dashboard_group = QtWidgets.QGroupBox("数据看板", parent)
        dashboard_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        dashboard_layout = QtWidgets.QVBoxLayout(dashboard_group)
        dashboard_layout.setContentsMargins(12, 28, 12, 10)
        dashboard_layout.setSpacing(8)

        self.greetingLabel = QtWidgets.QLabel("早上好，用户！", dashboard_group)
        self.greetingLabel.setProperty("dashboardGreeting", True)
        self.greetingLabel.setWordWrap(True)
        self.greetingLabel.setStyleSheet("")
        dashboard_layout.addWidget(self.greetingLabel)

        card_grid = QtWidgets.QGridLayout()
        card_grid.setHorizontalSpacing(8)
        card_grid.setVerticalSpacing(8)
        card_grid.setColumnStretch(0, 1)
        card_grid.setColumnStretch(1, 1)
        card_grid.setColumnStretch(2, 1)
        card_grid.setRowStretch(0, 1)
        card_grid.setRowStretch(1, 1)

        time_card, self.dashboardConnectionLabel, self.dashboardTimeSubLabel = (
            self._build_dashboard_card(dashboard_group, "当前时间", "--:--:--", "城市时间")
        )
        date_card, self.dashboardDateLabel, self.dashboardDateSubLabel = (
            self._build_dashboard_card(dashboard_group, "日期与星期", "----", "周几")
        )
        weather_card, self.dashboardWeatherLabel, self.dashboardWeatherSubLabel = (
            self._build_dashboard_card(dashboard_group, "当前城市 / 天气", "--", "等待刷新")
        )
        mode_card, self.dashboardModeLabel, self.dashboardModeSubLabel = (
            self._build_dashboard_card(dashboard_group, "昼夜模式", "--", "自动切换状态")
        )
        schedule_card, self.dashboardScheduleLabel, self.dashboardScheduleSubLabel = (
            self._build_dashboard_card(dashboard_group, "下次提醒", "无", "暂无倒计时")
        )
        system_card, self.dashboardSummaryLabel, self.dashboardTestLabel = (
            self._build_dashboard_card(dashboard_group, "系统状态", "显示 --", "提醒统计")
        )

        card_grid.addWidget(time_card, 0, 0)
        card_grid.addWidget(date_card, 0, 1)
        card_grid.addWidget(weather_card, 0, 2)
        card_grid.addWidget(mode_card, 1, 0)
        card_grid.addWidget(schedule_card, 1, 1)
        card_grid.addWidget(system_card, 1, 2)
        dashboard_layout.addLayout(card_grid)
        return dashboard_group

    def _build_alarm_schedule_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()
        outer.addWidget(self._build_board_alarm_group(host))
        outer.addWidget(self._build_schedule_management_group(host))
        outer.addStretch(1)
        return page

    def _build_debug_hardware_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("板端硬件测试", parent)
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(14, 24, 14, 12)
        layout.setSpacing(8)

        # Do not reuse the old widgets generated inside displayGroup: that group is
        # rebuilt for System Settings and Qt may keep/reparent/delete them, which
        # made the real exe show only labels without editors.
        self.debugBeepSpinBox = QtWidgets.QSpinBox(group)
        self.debugBeepSpinBox.setObjectName("debugBeepSpinBox")
        self.debugBeepSpinBox.setMinimum(10)
        self.debugBeepSpinBox.setMaximum(5000)
        self.debugBeepSpinBox.setSingleStep(100)
        self.debugBeepSpinBox.setValue(500)
        self.debugBeepSpinBox.setAlignment(QtCore.Qt.AlignCenter)
        self.debugBeepSpinBox.setMinimumWidth(132)
        self.debugBeepSpinBox.setMaximumWidth(220)

        self.debugLedHexEdit = QtWidgets.QLineEdit(group)
        self.debugLedHexEdit.setObjectName("debugLedHexEdit")
        self.debugLedHexEdit.setPlaceholderText("00-FF")
        self.debugLedHexEdit.setText(f"{self.runtime_state.led_mask:02X}")
        self.debugLedHexEdit.setAlignment(QtCore.Qt.AlignCenter)
        self.debugLedHexEdit.setClearButtonEnabled(False)
        self.debugLedHexEdit.setMaxLength(2)
        self.debugLedHexEdit.setMinimumWidth(132)
        self.debugLedHexEdit.setMaximumWidth(220)

        self.debugSendBeepButton = QtWidgets.QPushButton("触发蜂鸣", group)
        self.debugSendLedButton = QtWidgets.QPushButton("设置 LED", group)
        self.debugSendBeepButton.setObjectName("debugSendBeepButton")
        self.debugSendLedButton.setObjectName("debugSendLedButton")
        self.debugSendBeepButton.setMinimumWidth(118)
        self.debugSendLedButton.setMinimumWidth(118)

        # Keep legacy attribute names pointing at the visible debug controls so
        # older signal hookups and local-state sync code remain consistent.
        self.ui.beepSpinBox = self.debugBeepSpinBox
        self.ui.ledHexEdit = self.debugLedHexEdit
        self.ui.sendBeepButton = self.debugSendBeepButton
        self.ui.sendLedButton = self.debugSendLedButton

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, 86)
        grid.setColumnStretch(1, 1)

        def add_hardware_row(row: int, label_text: str, editor: QtWidgets.QWidget, button: QtWidgets.QPushButton) -> None:
            label = QtWidgets.QLabel(label_text, group)
            label.setMinimumWidth(82)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            editor.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            grid.addWidget(label, row, 0)
            grid.addWidget(editor, row, 1)
            grid.addWidget(button, row, 2)

        add_hardware_row(0, "蜂鸣(ms)", self.debugBeepSpinBox, self.debugSendBeepButton)
        add_hardware_row(1, "LED 掩码", self.debugLedHexEdit, self.debugSendLedButton)
        layout.addLayout(grid)
        hint = QtWidgets.QLabel(
            "蜂鸣用于快速听测板载蜂鸣器；LED 掩码输入 00-FF，发送 *SET:LED XX 后会短时手动覆盖 8 位 LED，便于验收逐位点亮，随后自动恢复系统状态。",
            group,
        )
        hint.setWordWrap(True)
        hint.setProperty("class", "infoChip")
        hint.setStyleSheet("")
        led_map_hint = QtWidgets.QLabel(
            "LED 位义：D1心跳  D2闹钟  D3编辑  D4串口RX  D5串口TX  D6夜间  D7RIGHT  D8手动覆盖；天气短显会临时使用天气掩码。",
            group,
        )
        led_map_hint.setWordWrap(True)
        led_map_hint.setProperty("class", "infoChip")
        led_map_hint.setStyleSheet("")
        layout.addWidget(hint)
        layout.addWidget(led_map_hint)
        return group

    def _build_debug_test_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()
        self._configure_sync_clock_group()
        outer.addWidget(self.ui.clockGroup)
        outer.addWidget(self._build_debug_hardware_group(host))
        self.ui.demoGroup.setTitle("调试与协议")
        outer.addWidget(self.ui.demoGroup)

        test_group = QtWidgets.QGroupBox("自动化测试", host)
        test_layout = QtWidgets.QVBoxLayout(test_group)
        test_layout.setContentsMargins(16, 32, 16, 14)
        test_layout.setSpacing(12)
        self.runChecksButton = QtWidgets.QPushButton("快速联合测试", test_group)
        self.runFullChecksButton = QtWidgets.QPushButton("全面联合测试", test_group)
        self.abortChecksButton = QtWidgets.QPushButton("中止测试", test_group)
        self.abortChecksButton.setEnabled(False)
        self.testStatusLabel = QtWidgets.QLabel("状态: 未运行", test_group)
        self.testStatusLabel.setProperty("class", "infoChip")
        self.testStatusLabel.setStyleSheet("")
        self.testEstimateLabel = QtWidgets.QLabel(
            f"预计耗时: 串口约 {estimated_duration_seconds(False)} 秒，本地模式约 {estimated_duration_seconds(True)} 秒",
            test_group,
        )
        self.testEstimateLabel.setProperty("class", "infoChip")
        self.testEstimateLabel.setStyleSheet("")
        self.testExplainLabel = QtWidgets.QLabel(
            "快速测试：串口心跳、FORMAT/MODE 查询、日期时间写入、昼夜模式切到另一侧再切回、铃声，以及 USER2 安全天气短显实测。",
            test_group,
        )
        self.testExplainLabel.setWordWrap(True)
        self.testExplainLabel.setProperty("class", "infoChip")
        self.testExplainLabel.setStyleSheet("")
        self.boardShortcutLabel = QtWidgets.QLabel(
            "全面测试：在快速测试基础上追加城市/NTP入口检查、跑马灯下划线、DISP/SPEED/FORMAT/EXT 按键、全部 ERROR 类型格式；USER2 安全短显会等待实际显示帧，测试不会改动自动昼夜开关最终状态。",
            test_group,
        )
        self.boardShortcutLabel.setWordWrap(True)
        self.boardShortcutLabel.setProperty("class", "infoChip")
        self.boardShortcutLabel.setStyleSheet("")
        self.testOutputText = QtWidgets.QTextEdit(test_group)
        self.testOutputText.setReadOnly(True)
        self.testOutputText.setMinimumHeight(220)
        self.testOutputText.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.testOutputText.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.testOutputText.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.testOutputText.setPlaceholderText("测试输出会显示在这里。")
        test_button_row = QtWidgets.QWidget(test_group)
        test_button_layout = QtWidgets.QHBoxLayout(test_button_row)
        test_button_layout.setContentsMargins(0, 0, 0, 0)
        test_button_layout.setSpacing(8)
        test_button_layout.addWidget(self.runChecksButton)
        test_button_layout.addWidget(self.runFullChecksButton)
        test_button_layout.addWidget(self.abortChecksButton)

        test_layout.addWidget(test_button_row)
        test_layout.addWidget(self.testStatusLabel)
        test_layout.addWidget(self.testEstimateLabel)
        test_layout.addWidget(self.testExplainLabel)
        test_layout.addWidget(self.boardShortcutLabel)
        test_layout.addWidget(self.testOutputText)

        outer.addWidget(test_group)
        outer.addStretch(1)
        return page

    def _prepare_widgets(self) -> None:
        now = datetime.now()
        default_target = self._default_near_future_datetime()
        self.ui.dateEdit.setDate(QtCore.QDate(now.year, now.month, now.day))
        self.ui.timeEdit.setTime(QtCore.QTime(now.hour, now.minute, now.second))
        self.ui.alarmTimeEdit.setTime(QtCore.QTime(default_target.hour, default_target.minute, 0))

        self.ui.displayToggleCombo.addItems(["ON", "OFF"])
        self.ui.formatCombo.addItems(["LEFT", "RIGHT"])
        self.ui.modeCombo.addItems(["DAY", "NIGHT"])
        for combo in (
            self.ui.portCombo,
            self.ui.displayToggleCombo,
            self.ui.formatCombo,
            self.ui.modeCombo,
        ):
            if combo is not self.ui.portCombo:
                combo.setProperty("stateField", True)
                combo.setFocusPolicy(QtCore.Qt.NoFocus)
            self._install_wheel_guard(combo)
        self._configure_port_combo()
        self.ui.ledHexEdit.setText("80")
        self.ui.beepSpinBox.setValue(500)
        self.ui.clearLogButton.hide()
        self.ui.exportLogButton.hide()
        for widget in (
            self.ui.dateEdit,
            self.ui.timeEdit,
            self.ui.alarmTimeEdit,
        ):
            widget.setAlignment(QtCore.Qt.AlignCenter)

        self._configure_protocol_test_group()
        self._apply_runtime_state_to_ui()

    def _protocol_command_templates(self) -> list[tuple[str, str]]:
        return [
            ("PING 心跳 | *PING", "*PING"),
            ("RST 软复位 | *RST", "*RST"),
            ("GET 日期 | *GET:DATE", "*GET:DATE"),
            ("GET 时间 | *GET:TIME", "*GET:TIME"),
            ("GET 闹钟 | *GET:ALARM", "*GET:ALARM"),
            ("GET 显示开关 | *GET:DISPLAY", "*GET:DISPLAY"),
            ("GET FORMAT | *GET:FORMAT", "*GET:FORMAT"),
            ("GET MODE | *GET:MODE", "*GET:MODE"),
            ("SET 日期 | *SET:DATE YEAR 2026 MONTH 6 DATE 9", "*SET:DATE YEAR 2026 MONTH 6 DATE 9"),
            ("SET 时间 | *SET:TIME HOUR 12 MINUTE 30 SECOND 45", "*SET:TIME HOUR 12 MINUTE 30 SECOND 45"),
            ("SET 闹钟 | *SET:ALARM HOUR 07 MINUTE 30 SECOND 00", "*SET:ALARM HOUR 07 MINUTE 30 SECOND 00"),
            ("关闭闹钟 | *SET:ALARM OFF", "*SET:ALARM OFF"),
            ("显示 ON | *SET:DISPLAY ON", "*SET:DISPLAY ON"),
            ("显示 OFF | *SET:DISPLAY OFF", "*SET:DISPLAY OFF"),
            ("FORMAT LEFT | *SET:FORMAT LEFT", "*SET:FORMAT LEFT"),
            ("FORMAT RIGHT | *SET:FORMAT RIGHT", "*SET:FORMAT RIGHT"),
            ("MODE DAY | *SET:MODE DAY", "*SET:MODE DAY"),
            ("MODE NIGHT | *SET:MODE NIGHT", "*SET:MODE NIGHT"),
            ("滚动消息 | *SET:MSG HELLO_CLOCK_DEMO", "*SET:MSG HELLO_CLOCK_DEMO"),
            ("蜂鸣 | *SET:BEEP 500", "*SET:BEEP 500"),
            ("LED 掩码 | *SET:LED 80", "*SET:LED 80"),
            ("天气短显 | *SET:WEATHER DISP SUN29C__ LED 05", "*SET:WEATHER DISP SUN29C__ LED 05"),
            ("铃声 DEFAULT | *SET:RING DEFAULT", "*SET:RING DEFAULT"),
            ("铃声 WORK_START | *SET:RING WORK_START", "*SET:RING WORK_START"),
            ("铃声 WORK_END | *SET:RING WORK_END", "*SET:RING WORK_END"),
            ("铃声 WAKE | *SET:RING WAKE", "*SET:RING WAKE"),
            ("铃声 SONG | *SET:RING SONG", "*SET:RING SONG"),
            ("模拟 USER1 | *SET:KEY USER1", "*SET:KEY USER1"),
            ("模拟 USER2 | *SET:KEY USER2", "*SET:KEY USER2"),
            ("模拟 DISP | *SET:KEY DISP", "*SET:KEY DISP"),
            ("模拟 SPEED | *SET:KEY SPEED", "*SET:KEY SPEED"),
            ("模拟 FORMAT | *SET:KEY FORMAT", "*SET:KEY FORMAT"),
            ("模拟 EXT | *SET:KEY EXT", "*SET:KEY EXT"),
            ("模拟 FUNC | *SET:KEY FUNC", "*SET:KEY FUNC"),
            ("模拟 SHIFT | *SET:KEY SHIFT", "*SET:KEY SHIFT"),
            ("模拟 ADD | *SET:KEY ADD", "*SET:KEY ADD"),
            ("模拟 SAVE | *SET:KEY SAVE", "*SET:KEY SAVE"),
            ("错误 LEN | *SET:MSG 40位超长消息", "*SET:MSG 1234567890123456789012345678901234567890"),
            ("错误 SYNTAX | *SET:TIME HOUR", "*SET:TIME HOUR"),
            ("错误 PARAM | *SET:MODE DUSK", "*SET:MODE DUSK"),
            ("错误 RANGE | *SET:TIME HOUR 99 MINUTE 00 SECOND 00", "*SET:TIME HOUR 99 MINUTE 00 SECOND 00"),
        ]

    def _configure_protocol_test_group(self) -> None:
        self.ui.demoGroup.setTitle("协议测试台")
        layout = self.ui.verticalLayout_3
        reusable = {
            self.ui.presetCombo,
            self.ui.rawCommandEdit,
            self.ui.sendPresetButton,
            self.ui.abbrevDemoButton,
            self.ui.mixedCaseDemoButton,
            self.ui.sendRawCommandButton,
        }
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in reusable:
                if widget.objectName() == "protocolActionRow":
                    for button in (
                        self.ui.sendPresetButton,
                        self.ui.abbrevDemoButton,
                        self.ui.mixedCaseDemoButton,
                    ):
                        button.setParent(self.ui.demoGroup)
                widget.setParent(None)
                widget.deleteLater()

        self.protocolHintLabel = QtWidgets.QLabel(
            "先选完整指令模板，可在文本框里手改；缩写和随机大小写都会作用到当前文本框，最终统一发送并在日志看 TX/RX。",
            self.ui.demoGroup,
        )
        self.protocolHintLabel.setProperty("class", "infoChip")
        self.protocolHintLabel.setWordWrap(True)
        self.protocolHintLabel.setStyleSheet("")

        self.ui.presetCombo.clear()
        for label, command in self._protocol_command_templates():
            self.ui.presetCombo.addItem(label, command)
        self.ui.presetCombo.setMinimumContentsLength(24)
        self.ui.presetCombo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.ui.rawCommandEdit.setPlaceholderText("当前指令，可直接编辑，例如 *GET:TIME")
        self.ui.rawCommandEdit.setClearButtonEnabled(False)

        self.ui.sendPresetButton.setText("发送当前指令")
        self.ui.abbrevDemoButton.setText("缩写当前指令")
        self.ui.mixedCaseDemoButton.setText("随机混合大小写")
        self.ui.sendRawCommandButton.setText("发送文本框")
        self.ui.sendRawCommandButton.hide()

        action_row = QtWidgets.QWidget(self.ui.demoGroup)
        action_row.setObjectName("protocolActionRow")
        action_layout = QtWidgets.QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(self.ui.sendPresetButton)
        action_layout.addWidget(self.ui.abbrevDemoButton)
        action_layout.addWidget(self.ui.mixedCaseDemoButton)

        layout.addWidget(self.protocolHintLabel)
        layout.addWidget(self.ui.presetCombo)
        layout.addWidget(self.ui.rawCommandEdit)
        layout.addWidget(action_row)
        self._apply_protocol_template(0)

    def _apply_protocol_template(self, index: int) -> None:
        command = self.ui.presetCombo.itemData(index)
        if not command:
            command = self.ui.presetCombo.currentText().strip()
        self.ui.rawCommandEdit.setText(str(command))

    def _abbreviate_protocol_command(self, command: str) -> str:
        replacements = {
            "DISPLAY": "DISP",
            "FORMAT": "FOR",
            "WEATHER": "WEAT",
            "DEFAULT": "DEF",
            "WORK_START": "WORK_S",
            "WORK_END": "WORK_E",
            "MINUTE": "MIN",
            "SECOND": "SEC",
            "MONTH": "MON",
        }
        parts = command.strip().split()
        if not parts:
            return command
        return " ".join(replacements.get(part.upper(), part) for part in parts)

    def abbreviate_current_protocol_command(self) -> None:
        command = self.ui.rawCommandEdit.text().strip()
        abbreviated = self._abbreviate_protocol_command(command)
        self.ui.rawCommandEdit.setText(abbreviated)
        self.log("INFO", f"已缩写当前指令: {abbreviated}")

    def randomize_current_protocol_command_case(self) -> None:
        command = self.ui.rawCommandEdit.text().strip()
        mixed = "".join(
            random.choice((ch.lower(), ch.upper())) if ch.isalpha() else ch
            for ch in command
        )
        self.ui.rawCommandEdit.setText(mixed)
        self.log("INFO", f"已随机混合大小写: {mixed}")

    def send_protocol_current_command(self) -> None:
        command = self.ui.rawCommandEdit.text().strip()
        if not command:
            return
        key = self._extract_key_command(command)
        if key is not None:
            self._send_key_command_safely(key, "协议台")
            return
        if command.upper() == "*RST" and (self.sync_in_progress or self.test_run_in_progress):
            self.send_command(command)
            return
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        self._mark_manual_serial_window(f"协议台: {command.split()[0]}", duration_s=2.0)
        if command.upper() == "*RST":
            self.pending_soft_reset_sync = True
            self.soft_reset_deadline_monotonic = time.monotonic() + 5.0
            self.log("INFO", "协议台发送 RST：等待板端复位响应，随后自动执行一次 NTP 对时。")
        self.send_command(command)

    def _build_extension_settings_page(self) -> QtWidgets.QScrollArea:
        page, host, outer = self._create_scroll_page()

        self.ui.displayGroup.setTitle("板端显示与快捷控制")
        self.usernameEdit = QtWidgets.QLineEdit(self.ui.displayGroup)
        self.usernameEdit.setPlaceholderText("默认 用户")
        self.usernameSaveButton = QtWidgets.QPushButton("确认用户名", self.ui.displayGroup)
        self._rebuild_system_settings_group()
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
        self.lookupCityButton = None
        self.saveExtensionConfigButton = None
        self.syncWeatherApplyButton = QtWidgets.QPushButton(
            "一键对时、刷新天气并应用",
            network_group,
        )
        self.autoDayNightCheck = QtWidgets.QCheckBox("自动昼夜模式", network_group)
        self.themeFollowCheck = QtWidgets.QCheckBox("PC 主题跟随板端模式", network_group)
        self.cityInfoLabel = QtWidgets.QLabel("经纬度: -- | 时区: --", network_group)
        self.cityInfoLabel.setProperty("class", "infoChip")
        self.networkTimeLabel = QtWidgets.QLabel("当前时间: --", network_group)
        self.networkTimeLabel.setProperty("class", "infoChip")
        self.weatherInfoLabel = QtWidgets.QLabel("天气: 未刷新", network_group)
        self.weatherInfoLabel.setProperty("class", "infoChip")
        self.sunriseSunsetLabel = QtWidgets.QLabel("日出/日落: -- / --", network_group)
        self.sunriseSunsetLabel.setProperty("class", "infoChip")
        self.ntpStatusLabel = QtWidgets.QLabel("最近 NTP: 未进行网络对时", network_group)
        self.ntpStatusLabel.setProperty("class", "infoChip")
        for label in (
            self.cityInfoLabel,
            self.networkTimeLabel,
            self.weatherInfoLabel,
            self.sunriseSunsetLabel,
            self.ntpStatusLabel,
        ):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored,
                QtWidgets.QSizePolicy.Preferred,
            )

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
        network_layout.addWidget(sync_button_row, 2, 1)
        network_layout.addWidget(checkbox_row_1, 3, 1)
        network_layout.addWidget(self.cityInfoLabel, 4, 1)
        network_layout.addWidget(self.networkTimeLabel, 5, 1)
        network_layout.addWidget(self.weatherInfoLabel, 6, 1)
        network_layout.addWidget(self.sunriseSunsetLabel, 7, 1)
        network_layout.addWidget(self.ntpStatusLabel, 8, 1)
        self._style_form_grid(network_layout, label_width=82, row_height=46)

        outer.addWidget(network_group)
        outer.addStretch(1)
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
        self.scheduleBoardLabelEdit.setMaxLength(32)
        self.scheduleBoardLabelEdit.setPlaceholderText("最多 32 字，超 8 位滚动")
        self.scheduleTimeEdit = QtWidgets.QTimeEdit(schedule_group)
        self.scheduleTimeEdit.setDisplayFormat("HH:mm:ss")
        default_target = self._default_near_future_datetime()
        self.scheduleTimeEdit.setTime(QtCore.QTime(default_target.hour, default_target.minute, 0))
        self.scheduleTypeCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleTypeCombo.addItems(["单次日期", "每周重复"])
        self.scheduleDateEdit = QtWidgets.QDateEdit(schedule_group)
        self.scheduleDateEdit.setCalendarPopup(True)
        self.scheduleDateEdit.setDate(QtCore.QDate(default_target.year, default_target.month, default_target.day))
        self.scheduleRingCombo = QtWidgets.QComboBox(schedule_group)
        self.scheduleRingCombo.addItems([label for _, label in self.ring_names])
        self.scheduleVoiceEdit = QtWidgets.QLineEdit(schedule_group)
        self.scheduleVoiceEdit.setPlaceholderText("语音播报文案，留空则不播报")
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

        button_row = QtWidgets.QWidget(schedule_group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleSaveButton)
        button_layout.addWidget(self.scheduleResetButton)

        form.addWidget(button_row, 8, 1)
        form.addWidget(self.scheduleDeleteButton, 9, 1)
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
        self.syncing_extension_widgets = True
        place = self._active_place()
        try:
            self.cityEdit.setText(place.name)
            self.placeSlotCombo.blockSignals(True)
            self.placeSlotCombo.setCurrentIndex(self.config.active_place_index)
            self.placeSlotCombo.blockSignals(False)
            for checkbox, value in (
                (self.autoDayNightCheck, self.config.auto_day_night),
                (self.themeFollowCheck, self.config.theme_follow_mode),
                (self.voiceEnabledCheck, self.config.voice_enabled),
                (self.quietNightCheck, self.config.quiet_night_rings),
            ):
                old_block = checkbox.blockSignals(True)
                checkbox.setChecked(value)
                checkbox.blockSignals(old_block)
            if hasattr(self, "usernameEdit"):
                self.usernameEdit.setText(self.config.user_name or "用户")
        finally:
            self.syncing_extension_widgets = False
        self.cityInfoLabel.setText(
            f"经纬度: {place.latitude:.4f}, {place.longitude:.4f} | 时区: {place.timezone}"
        )
        self.weatherInfoLabel.setText(f"天气: {self.weather_summary_text}")
        self._refresh_network_time_label()
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
                selected = ",".join(
                    weekday_names[index] for index in item.weekdays if 0 <= index < 7
                )
                rule = f"每周 {selected or '--'}"
            else:
                rule = f"单次 {item.target_date or '--'}"
            self.scheduleTable.setItem(row, 2, QtWidgets.QTableWidgetItem(rule))
            self.scheduleTable.setItem(row, 3, QtWidgets.QTableWidgetItem(item.trigger_time))
            self.scheduleTable.setItem(row, 4, QtWidgets.QTableWidgetItem(item.ring_type))

    def _refresh_single_alarm_ui(self) -> None:
        if not hasattr(self, "scheduleApplyAlarmButton"):
            return
        enabled = self.runtime_state.alarm_enabled or self.last_alarm == "RINGING"
        current_time = self.runtime_state.alarm_time or self.scheduleAlarmTimeEdit.time().toString("HH:mm:ss")
        if not enabled and self.scheduleAlarmTimeEdit.time().isValid():
            current_time = self.scheduleAlarmTimeEdit.time().toString("HH:mm:ss")
        if hasattr(self, "scheduleAlarmStatusLabel"):
            self.scheduleAlarmStatusLabel.setText(
                f"当前状态：{'开' if enabled else '关'}    触发时间：{current_time}"
            )
        self.scheduleApplyAlarmButton.setText("关闭" if enabled else "启用")
        self.scheduleApplyAlarmButton.setToolTip(
            "关闭当前板载单次闹钟" if enabled else "按上方时间启用板载单次闹钟"
        )

    def refresh_dashboard(self) -> None:
        if not hasattr(self, "dashboardSummaryLabel"):
            return
        now = self._selected_zone_now().replace(tzinfo=None)
        self.last_dashboard_minute = now.strftime("%Y-%m-%d %H:%M:%S")
        place = self._active_place()
        if self._is_local_mode_active():
            connection_text = "本地模式"
        elif self.is_connected:
            connection_text = f"已连接 {self.ui.portCombo.currentText()}"
        else:
            connection_text = "未连接"
        if hasattr(self, "serialStatusLabel"):
            self.serialStatusLabel.setText(f"串口状态：{connection_text}")
        enabled_count = len([item for item in self.schedules if item.enabled])
        next_schedule_text, next_schedule_time = self._next_reminder_summary(now)
        expected_mode = (
            "DAY" if should_use_day_mode(now, self.last_weather_snapshot) else "NIGHT"
        ) if self.last_weather_snapshot is not None else self.last_mode
        weather_text = self.weather_summary_text
        if self.last_weather_snapshot is not None:
            weather_text = (
                f"{weather_emoji(self.last_weather_snapshot.weather_code)} "
                f"{weather_code_summary(self.last_weather_snapshot.weather_code)} "
                f"{self.last_weather_snapshot.temperature_c:.1f}C"
            )
        city_time_text = now.strftime("%H:%M:%S")
        date_text = now.strftime("%Y-%m-%d")
        weekday_text_cn = self._weekday_chinese_name(now)
        alarm_state = "开" if self.last_alarm not in {"OFF", "RINGING", ""} else "关"
        self.dashboardSummaryLabel.setText(
            f"显示{'开' if self.runtime_state.display_on else '关'}"
        )
        self.dashboardConnectionLabel.setText(
            city_time_text
        )
        if hasattr(self, "dashboardTimeSubLabel"):
            self.dashboardTimeSubLabel.setText(f"{place.name} 城市时间")
        if hasattr(self, "dashboardDateLabel"):
            self.dashboardDateLabel.setText(
                date_text
            )
        if hasattr(self, "dashboardDateSubLabel"):
            self.dashboardDateSubLabel.setText(weekday_text_cn)
        self.dashboardWeatherLabel.setText(
            weather_text
        )
        if hasattr(self, "dashboardWeatherSubLabel"):
            self.dashboardWeatherSubLabel.setText(
                f"{place.name} | 日出 {self.sunrise_text} | 日落 {self.sunset_text} | {self._format_weather_age(now)}"
            )
        self.dashboardModeLabel.setText(
            self.last_mode
        )
        if hasattr(self, "dashboardModeSubLabel"):
            self.dashboardModeSubLabel.setText(
                f"当前所处 {'日间' if expected_mode == 'DAY' else '夜间'} | 自动昼夜 {'开' if self.config.auto_day_night else '关'}"
            )
        self.dashboardTestLabel.setText(
            f"板载闹钟 {alarm_state} | 提醒 {len(self.schedules)} 条，启用 {enabled_count} 条"
        )
        self.dashboardScheduleLabel.setText(
            next_schedule_text
        )
        if hasattr(self, "dashboardScheduleSubLabel"):
            self.dashboardScheduleSubLabel.setText(
                f"剩余 {self._format_dashboard_countdown(next_schedule_time, now)}"
            )
        if hasattr(self, "greetingLabel"):
            self.greetingLabel.setText(self._greeting_text(now))
        self._refresh_single_alarm_ui()
        self.status_connection.setText(connection_text)
        self.status_mode.setText(self.last_mode)
        self.status_location.setText(place.name)
        self.status_local_time.setText(datetime.now().strftime("%H:%M:%S"))
        if hasattr(self, "status_features") and not self.test_run_in_progress:
            self._restore_footer_features()
        dashboard_event_list = getattr(self, "dashboardEventList", None)
        if dashboard_event_list is not None:
            dashboard_event_list.clear()
            for entry in load_recent_event_logs(APP_DIR, limit=10):
                when = entry.get("when", "--")
                kind = entry.get("kind", "event")
                detail = entry.get("detail", "")
                dashboard_event_list.addItem(f"{when} | {kind} | {detail}")

    def _wire_signals(self) -> None:
        self.ui.refreshPortsButton.clicked.connect(self.refresh_ports)
        self.ui.portCombo.currentTextChanged.connect(self._remember_selected_port)
        self.ui.connectButton.clicked.connect(self.connect_and_apply_port)
        self.ui.disconnectButton.clicked.connect(self.disconnect_port)
        self.ui.applyDateButton.clicked.connect(self.apply_date)
        self.ui.queryDateButton.clicked.connect(lambda: self.send_command("*GET:DATE", "DATE"))
        self.ui.applyTimeButton.clicked.connect(self.apply_time)
        self.ui.queryTimeButton.clicked.connect(lambda: self.send_command("*GET:TIME", "TIME"))
        self.ui.applyAlarmButton.clicked.connect(self.apply_alarm)
        self.ui.disableAlarmButton.clicked.connect(
            self.disable_alarm
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
        self.ui.presetCombo.currentIndexChanged.connect(self._apply_protocol_template)
        self.ui.sendPresetButton.clicked.connect(self.send_protocol_current_command)
        self.ui.abbrevDemoButton.clicked.connect(self.abbreviate_current_protocol_command)
        self.ui.mixedCaseDemoButton.clicked.connect(self.randomize_current_protocol_command_case)
        self.ui.rawCommandEdit.returnPressed.connect(self.send_protocol_current_command)
        self.ui.sendRawCommandButton.clicked.connect(self.send_raw_command)
        self.ui.clearLogButton.clicked.connect(self.ui.logTextEdit.clear)
        self.ui.exportLogButton.clicked.connect(self.export_log)
        self.twin.virtual_key_requested.connect(self.send_virtual_key)
        self.placeSlotCombo.currentIndexChanged.connect(self.select_saved_place)
        if self.lookupCityButton is not None:
            self.lookupCityButton.clicked.connect(self.lookup_city)
        if self.saveExtensionConfigButton is not None:
            self.saveExtensionConfigButton.clicked.connect(self.save_extension_config)
        self.syncWeatherApplyButton.clicked.connect(
            lambda: self.sync_weather_and_apply(trigger_source="按钮", run_tests=False)
        )
        self.autoDayNightCheck.toggled.connect(self.set_auto_day_night_enabled)
        self.themeFollowCheck.toggled.connect(lambda _checked: self.save_extension_config(log_message=False))
        self.alarmVoiceEnabledCheck.toggled.connect(lambda _checked: self.save_extension_config(log_message=False))
        self.quietNightCheck.toggled.connect(lambda _checked: self.save_extension_config(log_message=False))
        self.usernameSaveButton.clicked.connect(self.save_user_name)
        if hasattr(self, "factoryResetButton"):
            self.factoryResetButton.clicked.connect(self.confirm_factory_reset)
        self.scheduleRingPreviewButton.clicked.connect(self.preview_schedule_ring)
        self.scheduleApplyAlarmButton.clicked.connect(self.toggle_schedule_alarm)
        self.scheduleDisableAlarmButton.clicked.connect(
            self.disable_alarm
        )
        self.scheduleQueryAlarmButton.clicked.connect(
            lambda: self.send_command("*GET:ALARM", "ALARM")
        )
        self.scheduleAlarmTimeEdit.timeChanged.connect(
            lambda _time: self._refresh_single_alarm_ui()
        )
        self.scheduleSaveButton.clicked.connect(self.save_schedule_item)
        self.scheduleDeleteButton.clicked.connect(self.delete_selected_schedule)
        self.scheduleTable.itemSelectionChanged.connect(self.load_selected_schedule)
        self.scheduleTable.itemDoubleClicked.connect(self.toggle_schedule_enabled)
        self.scheduleTable.viewport().installEventFilter(self)
        self.scheduleTypeCombo.currentIndexChanged.connect(self._sync_schedule_type_ui)
        self.runChecksButton.clicked.connect(lambda: self.run_automated_checks(full=False))
        self.runFullChecksButton.clicked.connect(lambda: self.run_automated_checks(full=True))
        self.abortChecksButton.clicked.connect(self.abort_automated_checks)

    def toggle_schedule_enabled(self, item: QtWidgets.QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self.schedules):
            next_enabled = not self.schedules[row].enabled
            if next_enabled and self.schedules[row].schedule_type == "once":
                now = self._selected_zone_now().replace(tzinfo=None)
                if self._next_schedule_time(self.schedules[row], now) is None:
                    self.log("WARN", "这条单次提醒已经过期，请先更新执行日期/时间后再启用。")
                    return
            self.schedules[row].enabled = next_enabled
            save_schedules(APP_DIR, self.schedules)
            self.refresh_schedule_table()
            self.refresh_dashboard()
            self.log("INFO", f"日程 '{self.schedules[row].title}' 已{'启用' if self.schedules[row].enabled else '禁用'}")

    def extension_tick(self) -> None:
        now = datetime.now()
        zone_now = self._selected_zone_now().replace(tzinfo=None)
        self._check_serial_health()
        self.status_local_time.setText(now.strftime("%H:%M:%S"))
        self._refresh_network_time_label()
        if not self.is_connected:
            self._refresh_local_twin_frame()
        current_dashboard_minute = zone_now.strftime("%Y-%m-%d %H:%M:%S")
        if current_dashboard_minute != self.last_dashboard_minute:
            self.refresh_dashboard()
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

    def _update_active_place_from_city_input(
        self,
        log_message: bool = True,
    ) -> bool:
        city = self.cityEdit.text().strip()
        if not city:
            if log_message:
                self.log("WARN", "城市名为空，未定位。")
            return False
        previous_place = SavedPlace(**self._active_place().__dict__)
        try:
            result = geocode_city(city)
        except Exception as exc:  # noqa: BLE001
            self.config.saved_places[self.config.active_place_index] = previous_place
            self.sync_extension_widgets_from_config()
            if log_message:
                self.log("ERROR", f"!!! 城市定位失败，已保留当前地点配置：{exc}")
            return False
        place = self._active_place()
        place.name = result.name
        place.latitude = result.latitude
        place.longitude = result.longitude
        place.timezone = result.timezone
        place.utc_offset_seconds = result.utc_offset_seconds
        self.config.saved_places[self.config.active_place_index] = place
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        if log_message:
            self.log("INFO", f"已定位城市: {result.name} ({result.latitude:.4f}, {result.longitude:.4f})")
        return True

    def lookup_city(self) -> None:
        if self._update_active_place_from_city_input(log_message=True):
            self.refresh_weather_and_push()

    def set_auto_day_night_enabled(self, checked: bool) -> None:
        if self.syncing_extension_widgets:
            return
        self.config.auto_day_night = checked
        save_config(APP_DIR, self.config)
        append_event_log(APP_DIR, "auto_day_night_toggle", "ON" if checked else "OFF")
        self.log("INFO", f"自动昼夜模式已{'开启' if checked else '关闭'}。")
        if checked:
            if hasattr(self, "autoModeNoticeLabel"):
                self.autoModeNoticeLabel.clear()
                self.autoModeNoticeLabel.setVisible(False)
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        self.refresh_dashboard()

    def save_user_name(self) -> None:
        name = self.usernameEdit.text().strip() if hasattr(self, "usernameEdit") else ""
        self.config.user_name = name or "用户"
        save_config(APP_DIR, self.config)
        append_event_log(APP_DIR, "user_name", self.config.user_name)
        self.log("INFO", f"用户名已更新为：{self.config.user_name}")
        self.refresh_dashboard()

    def save_extension_config(self, log_message: bool = True) -> None:
        place = self._active_place()
        place.name = self.cityEdit.text().strip() or place.name
        self.config.saved_places[self.config.active_place_index] = place
        self.config.theme_follow_mode = self.themeFollowCheck.isChecked()
        self.config.voice_enabled = self.voiceEnabledCheck.isChecked()
        self.config.quiet_night_rings = self.quietNightCheck.isChecked()
        self.config.auto_run_tests_on_start = False
        save_config(APP_DIR, self.config)
        self.sync_extension_widgets_from_config()
        self._refresh_theme_from_mode()
        if self.config.auto_day_night:
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        self.refresh_dashboard()
        if log_message:
            self.log("INFO", "扩展配置已保存。")

    def confirm_factory_reset(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认恢复出厂设置",
            "将重置城市、主题、显示、闹钟、日程和本地运行状态。日志文件会保留。是否继续？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            self.log("INFO", "已取消恢复出厂设置。")
            return
        self.restore_factory_defaults()

    def restore_factory_defaults(self) -> None:
        self.config = AppConfig()
        self.runtime_state = RuntimeState()
        self.schedules = []
        self.last_mode = self.runtime_state.mode
        self.last_alarm = "OFF"
        self.cached_weather_text = ""
        self.cached_weather_led_mask = 0
        self.weather_summary_text = "未刷新天气"
        self.sunrise_text = "--:--"
        self.sunset_text = "--:--"
        self.last_weather_snapshot = None
        self.last_ntp_sync_text = "未进行网络对时"
        self.last_selected_zone_time = "--:--:--"
        self._mark_board_weather_cache_dirty()
        save_config(APP_DIR, self.config)
        save_runtime_state(APP_DIR, self.runtime_state)
        save_schedules(APP_DIR, self.schedules)
        self.sync_extension_widgets_from_config()
        self._apply_runtime_state_to_ui()
        self.refresh_schedule_table()
        self.reset_schedule_form()
        self.refresh_dashboard()
        self.log("INFO", "已恢复出厂设置：配置、运行状态和日程已重置。")
        append_event_log(APP_DIR, "factory_reset", "defaults restored")
        if self.is_connected:
            self.pending_queries.clear()
            self._mark_manual_serial_window("恢复出厂设置", duration_s=3.0)
            self._send_serial_sequence(
                [
                    "*SET:KEY EXT",
                    "*SET:DISPLAY ON",
                    "*SET:FORMAT LEFT",
                    "*SET:MODE DAY",
                    "*SET:ALARM OFF",
                    "*SET:LED 00",
                ],
                gap_ms=260,
            )
            QtCore.QTimer.singleShot(1800, self.query_runtime_state)

    def _send_datetime_snapshot(
        self,
        moment: datetime,
        source_text: str,
        *,
        query_after: bool = False,
    ) -> bool:
        if self.sync_in_progress:
            return False
        self.sync_snapshot = moment.replace(microsecond=0)
        self._set_runtime_datetime(self.sync_snapshot)
        self.sync_in_progress = True
        self.sync_write_phase = "DATE"
        self.sync_date_retry_done = False
        self.sync_query_after_finish = query_after
        self.sync_watchdog_token += 1
        sync_token = self.sync_watchdog_token
        self.ui.syncNowButton.setEnabled(False)
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
        self.ui.dateEdit.setDate(
            QtCore.QDate(self.sync_snapshot.year, self.sync_snapshot.month, self.sync_snapshot.day)
        )
        self.ui.timeEdit.setTime(
            QtCore.QTime(self.sync_snapshot.hour, self.sync_snapshot.minute, self.sync_snapshot.second)
        )
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        self.send_command(
            build_set_date_command(self.sync_snapshot),
            allow_during_sync=True,
        )
        if not self.is_connected:
            QtCore.QTimer.singleShot(100, self._sync_host_time_step2)
        self.last_ntp_sync_text = source_text
        self.last_selected_zone_time = self.sync_snapshot.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, "ntpStatusLabel"):
            self.ntpStatusLabel.setText(f"最近 NTP: {source_text}")
        QtCore.QTimer.singleShot(
            5000,
            lambda token=sync_token: self._abort_sync_write_if_stale(token),
        )
        return True

    def _auto_sync_time_after_connect(self, trigger_source: str, query_after: bool = True) -> None:
        if not self.is_connected:
            return
        place, zone_now, offset_seconds = self._active_place_time_context()
        snapshot = zone_now.replace(tzinfo=None, microsecond=0)
        source_text = (
            f"{trigger_source}: {snapshot.strftime('%Y-%m-%d %H:%M:%S')} @ "
            f"{place.name} {place.timezone} {format_utc_offset(offset_seconds)} "
            "(PC/city time fallback)"
        )
        if self.sync_in_progress:
            self.log("WARN", f"{trigger_source}：当前已有对时写入流程，已跳过本次自动写时。")
            if query_after:
                QtCore.QTimer.singleShot(1200, self.query_runtime_state)
            return
        self.pending_queries.clear()
        if self._send_datetime_snapshot(snapshot, source_text, query_after=query_after):
            self.log(
                "INFO",
                f"{trigger_source}，已自动向板端同步当前时间："
                f"{snapshot.strftime('%Y-%m-%d %H:%M:%S')} | {place.name} | {place.timezone}",
            )
            append_event_log(APP_DIR, "auto_time_sync", source_text)
        else:
            self.log("WARN", f"{trigger_source}：自动同步当前时间未启动，请稍后手动 NTP 对时。")
        if query_after:
            QtCore.QTimer.singleShot(1400, self.query_runtime_state)

    def _abort_sync_write_if_stale(self, token: int) -> None:
        if token != self.sync_watchdog_token or not self.sync_in_progress:
            return
        self.sync_in_progress = False
        self.sync_snapshot = None
        self.sync_write_phase = ""
        self.sync_date_retry_done = False
        self.sync_query_after_finish = False
        self._restore_sync_buttons_if_idle()
        self.log("WARN", "对时写入超过 5 秒未收尾，已恢复界面；请检查串口响应和板端显示状态。")
        self.refresh_dashboard()
        self._maybe_run_pending_auto_test()
        self._drain_pending_lifecycle_ntp()

    def _handle_sync_write_ok(self) -> bool:
        if not self.sync_in_progress or not self.sync_write_phase:
            return False
        if self.sync_write_phase == "DATE":
            self.sync_write_phase = "TIME"
            QtCore.QTimer.singleShot(180, self._sync_host_time_step2)
            return True
        if self.sync_write_phase == "TIME":
            QtCore.QTimer.singleShot(160, self._finish_sync_host_time)
            return True
        return False

    def _handle_sync_write_error(self, parsed: ParsedLine) -> bool:
        if not self.sync_in_progress or not self.sync_write_phase:
            return False
        error_text = (parsed.data or parsed.name or "").strip().upper()
        phase = self.sync_write_phase
        expected_prefix = "*SET:DATE" if phase == "DATE" else "*SET:TIME"
        if not self.last_tx_command.strip().upper().startswith(expected_prefix):
            return False
        if (
            phase == "DATE"
            and error_text == "PARAM"
            and not self.sync_date_retry_done
            and self.sync_snapshot is not None
        ):
            self.sync_date_retry_done = True
            retry = (
                f"*SET:DATE YEAR {self.sync_snapshot.year:04d} "
                f"MONTH {self.sync_snapshot.month:02d} DATE {self.sync_snapshot.day:02d}"
            )
            self.log("WARN", "板端对 SET DATE 返回 ERROR PARAM，已用两位月/日兼容格式重试一次。")
            QtCore.QTimer.singleShot(
                220,
                lambda command=retry: self.send_command(command, allow_during_sync=True),
            )
            return True
        self.log("ERROR", f"对时写入在 {phase} 阶段失败: ERROR {error_text or 'UNKNOWN'}，已退出本次写入流程。")
        self.sync_in_progress = False
        self.sync_snapshot = None
        self.sync_write_phase = ""
        self.sync_date_retry_done = False
        self.sync_query_after_finish = False
        self._restore_sync_buttons_if_idle()
        self.refresh_dashboard()
        self._maybe_run_pending_auto_test()
        self._drain_pending_lifecycle_ntp()
        return True

    def _clear_waiting_serial_state(self, reason: str) -> None:
        had_sync = self.sync_in_progress or bool(self.sync_write_phase)
        self.pending_queries.clear()
        self.read_buffer = ""
        self.last_ping_monotonic = None
        if had_sync:
            self.sync_in_progress = False
            self.sync_snapshot = None
            self.sync_write_phase = ""
            self.sync_date_retry_done = False
            self.sync_query_after_finish = False
            self._restore_sync_buttons_if_idle()
            self.log("WARN", f"{reason}：已退出未完成的对时写入流程，避免继续卡住。")
        if self.pending_soft_reset_sync:
            self.pending_soft_reset_sync = False
            self.soft_reset_deadline_monotonic = 0.0

    def _write_serial_recovery_command(self, command: str) -> bool:
        if not self.is_connected:
            return False
        cleaned = command.strip()
        try:
            with self.serial_io_lock:
                self.serial_port.write((cleaned + "\r\n").encode("ascii", "ignore"))
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"串口恢复指令发送失败: {exc}")
            self.disconnect_port(log_message=False)
            return False
        self.last_tx_command = cleaned
        self.last_tx_monotonic = time.perf_counter()
        self.log("TX", cleaned)
        return True

    def _check_serial_health(self) -> None:
        if not self.is_connected or self.last_tx_monotonic <= 0.0:
            return
        now = time.perf_counter()
        last_rx = self.last_serial_rx_monotonic
        if last_rx >= self.last_tx_monotonic:
            self.serial_recovery_stage = 0
            return
        quiet_s = now - self.last_tx_monotonic
        if quiet_s < 3.8:
            return
        if now - self.last_serial_recovery_monotonic < 3.5:
            return
        self.last_serial_recovery_monotonic = now
        self.serial_recovery_stage += 1
        reason = f"串口 TX 后 {quiet_s:.1f}s 未收到 RX（最近指令: {self.last_tx_command or '--'}）"
        self._clear_waiting_serial_state(reason)
        test_timeout_s = estimated_duration_seconds(False, full=self.test_run_full) + 45.0
        if self.test_run_in_progress and now - self.test_run_started_at > test_timeout_s:
            if self.test_cancel_event is not None:
                self.test_cancel_event.set()
            self.test_run_in_progress = False
            self.test_run_full = False
            self.runChecksButton.setEnabled(True)
            if hasattr(self, "runFullChecksButton"):
                self.runFullChecksButton.setEnabled(True)
            if hasattr(self, "abortChecksButton"):
                self.abortChecksButton.setEnabled(False)
            self.poll_timer.start()
            self.ping_timer.start()
            if hasattr(self, "status_features"):
                self._restore_footer_features()
            self.testStatusLabel.setText("状态: 失败")
            self.testOutputText.append(
                f"\nFAIL\n{reason}，已终止自动测试并恢复串口轮询。疑似硬件端状态机卡死，请手动 RESET。"
            )
            self.log("ERROR", f"{reason}，自动测试已超时退出；疑似硬件端状态机卡死，请手动 RESET。")
            return
        if self.serial_recovery_stage == 1:
            self.log("WARN", f"{reason}，正在自动发送 EXT/PING 尝试退出临时显示或异常等待态。")
            self._write_serial_recovery_command("*SET:KEY EXT")
            QtCore.QTimer.singleShot(260, lambda: self._write_serial_recovery_command("*PING"))
            return
        if self.serial_recovery_stage == 2:
            self.log("ERROR", f"{reason}，第一次恢复无响应，正在尝试软 RST；若仍无 RX 再手动检查 USB/板端。")
            self.pending_soft_reset_sync = True
            self.soft_reset_deadline_monotonic = time.monotonic() + 5.0
            self._write_serial_recovery_command("*RST")
            return
        self.log("ERROR", f"{reason}，自动恢复仍无响应；已清理 PC 等待态，请检查板端供电/串口或手动 RESET。")

    def request_user1_time_sync(self, trigger_source: str) -> None:
        now = time.monotonic()
        if "USER1" in trigger_source.upper() and now - self.last_user1_ntp_request_monotonic < 4.0:
            if now - self.last_user1_ntp_warn_monotonic > 2.0:
                self.log("INFO", f"{trigger_source} 连续触发过快，本次 NTP 已合并/忽略，防止串口对时风暴。")
                self.last_user1_ntp_warn_monotonic = now
            return
        if self.ntp_fetch_in_progress or self.sync_in_progress:
            if "USER1" in trigger_source.upper():
                self.last_user1_ntp_request_monotonic = now
            self.log("WARN", f"{trigger_source} 请求对时，但当前已有对时流程正在进行，已合并为当前流程。")
            return
        if "USER1" in trigger_source.upper():
            self.last_user1_ntp_request_monotonic = now
        self.log("INFO", f"{trigger_source} 请求 PC 侧 NTP 对时。")
        append_event_log(APP_DIR, "user1_sync_request", trigger_source)
        self._set_latest_event(f"{trigger_source} 请求 NTP 对时")
        self.sync_ntp_time(trigger_source=trigger_source)

    def _emit_signal_safe(self, signal_name: str, *args) -> None:
        try:
            getattr(self, signal_name).emit(*args)
        except RuntimeError:
            pass

    def _serial_auto_quiet_active(self) -> bool:
        return time.monotonic() < self.auto_serial_quiet_until

    def _serial_auto_quiet_delay_ms(self, padding_ms: int = 160) -> int:
        remaining = max(0.0, self.auto_serial_quiet_until - time.monotonic())
        return int(remaining * 1000) + padding_ms

    def _mark_manual_serial_window(self, reason: str, duration_s: float = 1.8) -> None:
        self.auto_serial_quiet_until = max(
            self.auto_serial_quiet_until,
            time.monotonic() + duration_s,
        )
        self.auto_serial_quiet_reason = reason

    def _current_weather_short_token(self) -> tuple[str, int]:
        token = (self.cached_weather_text or self.runtime_state.weather_token or "").strip()
        led_mask = self.cached_weather_led_mask or self.runtime_state.weather_led_mask
        if not token:
            return "NO_WX___", 0
        return token, led_mask & 0xFF

    def _mark_board_weather_cache_dirty(self) -> None:
        self.board_weather_cache_token = ""
        self.board_weather_cache_led_mask = -1
        self.board_weather_cache_synced_monotonic = 0.0

    def _mark_board_weather_cache_synced(self, token: str, led_mask: int) -> None:
        self.board_weather_cache_token = token
        self.board_weather_cache_led_mask = led_mask & 0xFF
        self.board_weather_cache_synced_monotonic = time.monotonic()

    def _board_weather_cache_is_fresh(self, token: str, led_mask: int) -> bool:
        if not token:
            return False
        if self.board_weather_cache_token != token:
            return False
        if self.board_weather_cache_led_mask != (led_mask & 0xFF):
            return False
        return (time.monotonic() - self.board_weather_cache_synced_monotonic) < 600.0

    def _has_real_weather_cache(self) -> bool:
        return bool((self.cached_weather_text or self.runtime_state.weather_token or "").strip())

    def _push_weather_cache_to_board_if_available(self, source_text: str) -> None:
        if not self._has_real_weather_cache():
            return
        self._push_weather_cache_to_board(source_text)

    def _send_serial_sequence(
        self,
        commands: list[str],
        *,
        gap_ms: int = 180,
        allow_during_sync: bool = False,
        allow_during_test: bool = False,
    ) -> None:
        for index, command in enumerate(commands):
            QtCore.QTimer.singleShot(
                index * gap_ms,
                lambda command=command: self.send_command(
                    command,
                    allow_during_sync=allow_during_sync,
                    allow_during_test=allow_during_test,
                ),
            )

    def _board_message_payload(self, visible_text: str) -> str:
        payload = visible_text.strip()
        if self.runtime_state.format == "RIGHT":
            return payload[::-1]
        return payload

    def _send_user2_weather_message(self, source_text: str, visible_text: str, led_mask: int) -> None:
        message_text = visible_text.replace("_", " ").strip() or "NO WX"
        board_message_text = message_text
        self._mark_manual_serial_window(source_text, duration_s=4.5)
        self._mark_user2_display_pending(message_text)
        self.runtime_state.format = "LEFT"
        self.ui.formatCombo.setCurrentText("LEFT")
        self._save_runtime_state()
        commands = ["*SET:DISPLAY ON", "*SET:FORMAT LEFT"]
        if led_mask:
            commands.append(f"*SET:LED {led_mask & 0xFF:02X}")
        commands.append(f"*SET:MSG {board_message_text}")
        self._send_serial_sequence(commands, gap_ms=180)

    def _compact_display_text(self, text: str) -> str:
        return "".join(ch for ch in text.upper() if ch.isalnum())

    def _display_event_token_is_valid(self, token: str) -> bool:
        if not token or len(token) > 8:
            return False
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_~- ")
        return all((32 <= ord(ch) < 127) and (ch.upper() in allowed) for ch in token)

    def _mark_user2_display_pending(self, visible_text: str) -> None:
        compact = self._compact_display_text(visible_text)
        self.pending_user2_display_text = compact or "NOWX"
        self.pending_user2_display_deadline = time.monotonic() + 2.2
        self.pending_user2_display_fallback_done = False
        self.pending_user2_display_retry_count = 0
        QtCore.QTimer.singleShot(2250, self._fallback_user2_weather_display_if_needed)

    def _clear_user2_display_pending_if_matched(self, visible_text: str) -> None:
        if not self.pending_user2_display_text:
            return
        compact = self._compact_display_text(visible_text)
        expected = self.pending_user2_display_text
        if expected in compact:
            self.pending_user2_display_text = ""
            self.pending_user2_display_deadline = 0.0
            self.pending_user2_display_fallback_done = False
            self.pending_user2_display_retry_count = 0

    def _fallback_user2_weather_display_if_needed(self) -> None:
        if (
            not self.is_connected
            or not self.pending_user2_display_text
            or time.monotonic() < self.pending_user2_display_deadline
        ):
            return
        fallback_text = self.pending_user2_display_text[:32]
        self.pending_user2_display_retry_count += 1
        if self.pending_user2_display_retry_count > 2:
            self.log("ERROR", f"USER2 天气短显连续恢复失败，已释放等待状态；最后期望显示 {fallback_text}。")
            self.pending_user2_display_fallback_done = True
            self.pending_user2_display_text = ""
            self.pending_user2_display_deadline = 0.0
            self.pending_user2_display_retry_count = 0
            return
        self.pending_user2_display_deadline = time.monotonic() + 1.4
        self.log("WARN", f"USER2 天气短显未观察到期望帧，正在第 {self.pending_user2_display_retry_count} 次自动恢复显示 {fallback_text}。")
        self._write_serial_recovery_command("*SET:KEY EXT")
        board_text = fallback_text
        QtCore.QTimer.singleShot(
            220,
            lambda: self._send_serial_sequence(
                ["*SET:DISPLAY ON", "*SET:FORMAT LEFT", f"*SET:MSG {board_text}"],
                gap_ms=180,
            ),
        )
        QtCore.QTimer.singleShot(1500, self._fallback_user2_weather_display_if_needed)

    def _push_weather_cache_to_board(self, source_text: str, retry_count: int = 0) -> None:
        if not self.is_connected:
            return
        if (
            self.ntp_fetch_in_progress
            or self.sync_in_progress
            or self.test_run_in_progress
            or self._serial_auto_quiet_active()
        ):
            if retry_count == 0:
                self.log("INFO", f"{source_text}：串口正忙，天气缓存下发已排队。")
            if retry_count < 12:
                delay_ms = self._serial_auto_quiet_delay_ms(220) if self._serial_auto_quiet_active() else 420
                QtCore.QTimer.singleShot(
                    delay_ms,
                    lambda: self._push_weather_cache_to_board(source_text, retry_count + 1),
                )
            else:
                self.log("ERROR", f"{source_text}：串口忙超过 5 秒，天气缓存未能下发到板端。")
            return
        token, led_mask = self._current_weather_short_token()
        self._mark_manual_serial_window(source_text, duration_s=0.8)
        self.send_command(build_set_weather_command(token, led_mask))
        self._mark_board_weather_cache_synced(token, led_mask)

    def _trigger_user2_weather_short_display(
        self,
        source_text: str,
        *,
        retry_count: int = 0,
    ) -> None:
        now = time.monotonic()
        if self.test_run_in_progress and retry_count == 0:
            self.log("WARN", f"{source_text}：自动测试正在占用串口，本次 USER2 人工短显已忽略。")
            return
        if retry_count == 0 and now - self.last_user2_trigger_monotonic < 1.2:
            self.log("INFO", f"{source_text}：USER2 天气短显触发过密，已合并本次请求。")
            return
        token, led_mask = self._current_weather_short_token()
        visible = token.replace("_", " ").strip() or "NO WX"
        has_weather_cache = bool(
            (self.cached_weather_text or self.runtime_state.weather_token or "").strip()
        )
        if retry_count == 0:
            self.last_user2_trigger_monotonic = now
        if not self.is_connected:
            self._set_local_display_override(visible, 5.0, led_mask)
            if has_weather_cache:
                self.log("INFO", f"{source_text}：本地模式显示天气短显 {visible}，约 5 秒后回到时钟。")
            else:
                self.log("WARN", f"{source_text}：当前没有天气缓存，本地显示 NO WX。")
            self.refresh_dashboard()
            return
        if (
            self.ntp_fetch_in_progress
            or self.sync_in_progress
            or self.test_run_in_progress
            or self._serial_auto_quiet_active()
        ):
            if retry_count == 0:
                self.log("INFO", f"{source_text}：串口正忙，天气短显已排队，避免插队打断对时/测试。")
            if retry_count < 12:
                delay_ms = self._serial_auto_quiet_delay_ms(240) if self._serial_auto_quiet_active() else 360
                QtCore.QTimer.singleShot(
                    delay_ms,
                    lambda: self._trigger_user2_weather_short_display(
                        source_text,
                        retry_count=retry_count + 1,
                    ),
                )
            else:
                self.log("ERROR", f"{source_text}：串口忙超过 4 秒，已取消本次 USER2 天气短显。")
            return
        self.last_user2_trigger_monotonic = now
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        if has_weather_cache:
            self.log("INFO", f"{source_text}：使用安全天气短显显示 {visible}，避免重复触发板端 USER2 状态机。")
        else:
            self.log("WARN", f"{source_text}：当前没有天气缓存，使用安全天气短显显示 NO WX。")
        self._send_user2_weather_message(source_text, visible, led_mask)
        QtCore.QTimer.singleShot(1000, self.query_runtime_state)

    def _schedule_lifecycle_ntp(
        self,
        trigger_source: str,
        *,
        delay_ms: int = 0,
        fallback_on_fail: bool = True,
        query_after: bool = False,
    ) -> None:
        if not self.is_connected:
            return
        if delay_ms > 0:
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda: self._schedule_lifecycle_ntp(
                    trigger_source,
                    fallback_on_fail=fallback_on_fail,
                    query_after=query_after,
                ),
            )
            return
        if self._serial_auto_quiet_active():
            QtCore.QTimer.singleShot(
                self._serial_auto_quiet_delay_ms(),
                lambda: self._schedule_lifecycle_ntp(
                    trigger_source,
                    fallback_on_fail=fallback_on_fail,
                    query_after=query_after,
                ),
            )
            return
        if self.ntp_fetch_in_progress or self.sync_in_progress or self.weather_refresh_in_progress:
            self.pending_lifecycle_ntp_source = trigger_source
            self.pending_lifecycle_ntp_query_after = (
                self.pending_lifecycle_ntp_query_after or query_after
            )
            self.pending_lifecycle_ntp_fallback = (
                self.pending_lifecycle_ntp_fallback or fallback_on_fail
            )
            self.log("INFO", f"{trigger_source}：已有后台任务进行中，已合并为稍后一次 NTP 对时。")
            QtCore.QTimer.singleShot(900, self._drain_pending_lifecycle_ntp)
            return
        if time.monotonic() - self.last_lifecycle_ntp_monotonic < 2.2:
            self.log("INFO", f"{trigger_source}：已与刚刚的 NTP 对时合并，避免重复对时。")
            if query_after:
                QtCore.QTimer.singleShot(900, self.query_runtime_state)
            return
        if self.sync_ntp_time(
            trigger_source=trigger_source,
            fallback_on_fail=fallback_on_fail,
            query_after=query_after,
        ):
            self.last_lifecycle_ntp_monotonic = time.monotonic()

    def _drain_pending_lifecycle_ntp(self) -> None:
        if not self.pending_lifecycle_ntp_source:
            return
        source = self.pending_lifecycle_ntp_source
        query_after = self.pending_lifecycle_ntp_query_after
        fallback = self.pending_lifecycle_ntp_fallback
        self.pending_lifecycle_ntp_source = ""
        self.pending_lifecycle_ntp_query_after = False
        self.pending_lifecycle_ntp_fallback = False
        self._schedule_lifecycle_ntp(
            source,
            fallback_on_fail=fallback,
            query_after=query_after,
        )

    def _fallback_write_current_city_time(self, trigger_source: str, query_after: bool) -> None:
        if not self.is_connected:
            self._restore_sync_buttons_if_idle()
            self.refresh_dashboard()
            self._maybe_run_pending_auto_test()
            return
        self.log("WARN", f"{trigger_source}：NTP 不可用，改用当前城市/PC 时间写入板端。")
        self._auto_sync_time_after_connect(
            f"{trigger_source} NTP 失败 fallback",
            query_after=query_after,
        )

    def sync_ntp_time(
        self,
        trigger_source: str = "按钮",
        *,
        fallback_on_fail: bool = False,
        query_after: bool = False,
    ) -> bool:
        if self._serial_auto_quiet_active():
            self.log(
                "INFO",
                f"{trigger_source}：等待手动串口指令完成后再执行 NTP，避免串口命令插队。",
            )
            QtCore.QTimer.singleShot(
                self._serial_auto_quiet_delay_ms(),
                lambda: self.sync_ntp_time(
                    trigger_source=trigger_source,
                    fallback_on_fail=fallback_on_fail,
                    query_after=query_after,
                ),
            )
            return False
        if (
            self.ntp_fetch_in_progress
            or self.sync_in_progress
            or self.weather_refresh_in_progress
            or self.test_run_in_progress
        ):
            if self.test_run_in_progress:
                self.log("WARN", f"{trigger_source}：自动测试正在占用串口，本次 NTP 请求已忽略。")
                return False
            if self.weather_refresh_in_progress:
                self.pending_lifecycle_ntp_source = trigger_source
                self.pending_lifecycle_ntp_query_after = (
                    self.pending_lifecycle_ntp_query_after or query_after
                )
                self.pending_lifecycle_ntp_fallback = (
                    self.pending_lifecycle_ntp_fallback or fallback_on_fail
                )
                QtCore.QTimer.singleShot(900, self._drain_pending_lifecycle_ntp)
            self.log("WARN", f"{trigger_source} 请求对时，但当前已有对时流程正在进行。")
            return False
        place = self._active_place()
        self.ntp_fetch_in_progress = True
        self.ntp_watchdog_token += 1
        ntp_token = self.ntp_watchdog_token
        self.ntp_fallback_on_fail = fallback_on_fail
        self.ntp_query_after = query_after
        self.ntp_active_source = trigger_source
        self.ui.syncNowButton.setEnabled(False)
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
        self.log("INFO", f"开始 NTP 对时：{place.name} | {place.timezone} | 时间口径 NTP UTC -> 城市时区。")

        def worker() -> None:
            snapshot_utc = None
            error = None
            try:
                snapshot_utc = fetch_ntp_time(self.config.ntp_host)
            except Exception as exc:  # noqa: BLE001
                error = exc
            self._emit_signal_safe("ntp_sync_finished", snapshot_utc, error, trigger_source, ntp_token)

        threading.Thread(target=worker, daemon=True).start()
        QtCore.QTimer.singleShot(
            8000,
            lambda token=ntp_token, source=trigger_source: self._abort_ntp_if_stale(token, source),
        )
        return True

    def _abort_ntp_if_stale(self, token: int, trigger_source: str) -> None:
        if token != self.ntp_watchdog_token or not self.ntp_fetch_in_progress:
            return
        fallback = self.ntp_fallback_on_fail
        query_after = self.ntp_query_after
        self.ntp_fetch_in_progress = False
        self.ntp_fallback_on_fail = False
        self.ntp_query_after = False
        self.ntp_active_source = ""
        self._restore_sync_buttons_if_idle()
        self.log("ERROR", f"NTP 对时超过 8 秒未返回，已取消等待（来源: {trigger_source}）。")
        if fallback:
            self._fallback_write_current_city_time(trigger_source, query_after)
            return
        self.refresh_dashboard()
        self._maybe_run_pending_auto_test()
        self._drain_pending_lifecycle_ntp()

    def _finish_ntp_sync(self, snapshot_utc, error, trigger_source: str, token: int) -> None:
        if token != self.ntp_watchdog_token:
            return
        if not self.ntp_fetch_in_progress and self.sync_snapshot is None:
            return
        fallback = self.ntp_fallback_on_fail
        query_after = self.ntp_query_after
        self.ntp_fetch_in_progress = False
        self.ntp_fallback_on_fail = False
        self.ntp_query_after = False
        self.ntp_active_source = ""
        if error is not None or snapshot_utc is None:
            message = str(error) if error is not None else "NTP snapshot missing"
            self.log("ERROR", f"NTP 对时失败: {message}")
            if "启动" in trigger_source or "USER1" in trigger_source:
                self.log("WARN", "板端启动后若仍停在默认时间，可视为本次 NTP 对时失败。")
            append_event_log(APP_DIR, "ntp_error", message)
            if fallback:
                self._fallback_write_current_city_time(trigger_source, query_after)
                return
            self._restore_sync_buttons_if_idle()
            self.refresh_dashboard()
            self._maybe_run_pending_auto_test()
            self._drain_pending_lifecycle_ntp()
            return
        place, zone_snapshot, offset_seconds = self._active_place_time_context(snapshot_utc)
        snapshot = zone_snapshot.replace(tzinfo=None)
        source_text = (
            f"{snapshot.strftime('%Y-%m-%d %H:%M:%S')} @ {place.name} "
            f"{place.timezone} {format_utc_offset(offset_seconds)}"
        )
        self._send_datetime_snapshot(snapshot, source_text, query_after=query_after)
        append_event_log(APP_DIR, "ntp_sync", f"{trigger_source} -> {source_text}")
        if self.is_connected:
            self.log("INFO", f"NTP 对时成功并写入 S800（来源: {trigger_source}；时间口径: NTP UTC -> 城市时区）。")
        else:
            self.log("WARN", f"!!! 已完成上位机本地对时（来源: {trigger_source}；时间口径: NTP UTC -> 城市时区），当前未下发板端。")
        self.refresh_dashboard()

    def _restore_sync_buttons_if_idle(self) -> None:
        if self.ntp_fetch_in_progress or self.sync_in_progress:
            return
        self.ui.syncNowButton.setEnabled(True)
        if (
            not self.weather_refresh_in_progress
            and hasattr(self, "syncWeatherApplyButton")
        ):
            self.syncWeatherApplyButton.setEnabled(True)
            self.syncWeatherApplyButton.setText("一键对时、刷新天气并应用")

    def _maybe_run_pending_auto_test(self) -> None:
        if not self.pending_auto_test_after_apply:
            return
        if self.ntp_fetch_in_progress or self.sync_in_progress or self.weather_refresh_in_progress:
            return
        self.pending_auto_test_after_apply = False
        full = self.pending_auto_test_full
        self.pending_auto_test_full = False
        QtCore.QTimer.singleShot(500, lambda full=full: self.run_automated_checks(full=full))

    def sync_weather_and_apply(
        self,
        trigger_source: str = "按钮",
        run_tests: bool | None = None,
    ) -> None:
        if self.test_run_in_progress:
            self.log("WARN", f"{trigger_source}：自动测试正在占用串口，本次天气/NTP 一键流程已忽略。")
            return
        self.log("INFO", "已开始更新，请稍等片刻；正在后台定位城市、刷新天气，随后进行 NTP 对时写入。")
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
            self.syncWeatherApplyButton.setText("更新中...")
        self.pending_auto_test_after_apply = bool(run_tests)
        self.weather_timeout_ntp_source = trigger_source
        self.refresh_weather_and_push(
            log_trigger=True,
            city_text=self.cityEdit.text().strip() if hasattr(self, "cityEdit") else "",
            resolve_city=True,
            trigger_source=trigger_source,
            run_ntp_after_resolve=True,
        )

    def refresh_weather_and_push(
        self,
        log_trigger: bool = True,
        *,
        city_text: str = "",
        resolve_city: bool = False,
        trigger_source: str = "自动刷新",
        run_ntp_after_resolve: bool = False,
    ) -> None:
        if self.test_run_in_progress:
            if log_trigger:
                self.log("WARN", f"{trigger_source}：自动测试正在占用串口，本次天气刷新已忽略。")
            return
        if self.weather_refresh_in_progress:
            if log_trigger:
                self.log("WARN", "天气刷新仍在进行，已忽略本次重复请求。")
            return
        self.weather_refresh_in_progress = True
        self.weather_watchdog_token += 1
        weather_token = self.weather_watchdog_token
        base_place = SavedPlace(**self._active_place().__dict__)
        city_name = city_text.strip() or base_place.name
        if hasattr(self, "weatherInfoLabel"):
            self.weatherInfoLabel.setText(f"天气: 正在更新 {city_name} ...")
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
            self.syncWeatherApplyButton.setText("更新中...")

        def worker() -> None:
            snapshot = None
            error = None
            resolved_place = SavedPlace(**base_place.__dict__)
            resolved_ok = not resolve_city
            try:
                if resolve_city and city_name:
                    result = geocode_city(city_name)
                    resolved_place = SavedPlace(
                        name=result.name,
                        latitude=result.latitude,
                        longitude=result.longitude,
                        timezone=result.timezone,
                        utc_offset_seconds=result.utc_offset_seconds,
                    )
                    resolved_ok = True
                snapshot = fetch_weather_snapshot(
                    resolved_place.name,
                    resolved_place.latitude,
                    resolved_place.longitude,
                    resolved_place.timezone,
                )
            except Exception as exc:  # noqa: BLE001
                error = exc
            context = {
                "place": resolved_place if resolved_ok else None,
                "trigger_source": trigger_source,
                "run_ntp_after_resolve": run_ntp_after_resolve,
                "city_name": city_name,
            }
            self._emit_signal_safe("weather_refresh_finished", snapshot, error, log_trigger, weather_token, context)

        threading.Thread(target=worker, daemon=True).start()
        QtCore.QTimer.singleShot(
            14000,
            lambda token=weather_token: self._abort_weather_if_stale(token),
        )

    def _abort_weather_if_stale(self, token: int) -> None:
        if token != self.weather_watchdog_token or not self.weather_refresh_in_progress:
            return
        self.weather_refresh_in_progress = False
        self.pending_auto_test_after_apply = False
        self._restore_sync_buttons_if_idle()
        self.log("ERROR", "天气刷新超过 14 秒未返回，已取消等待；请检查网络、代理或天气接口。")
        append_event_log(APP_DIR, "weather_timeout", "14s")
        self.refresh_dashboard()
        timeout_source = self.weather_timeout_ntp_source
        self.weather_timeout_ntp_source = ""
        if timeout_source:
            self.sync_ntp_time(trigger_source=timeout_source)

    def _finish_weather_refresh(self, snapshot, error, log_trigger: bool, token: int, context: object = None) -> None:
        if token != self.weather_watchdog_token:
            return
        self.weather_refresh_in_progress = False
        if bool((context if isinstance(context, dict) else {}).get("run_ntp_after_resolve")):
            self.weather_timeout_ntp_source = ""
        self._restore_sync_buttons_if_idle()
        ctx = context if isinstance(context, dict) else {}
        resolved_place = ctx.get("place")
        trigger_source = str(ctx.get("trigger_source") or "天气刷新")
        run_ntp_after_resolve = bool(ctx.get("run_ntp_after_resolve"))
        if isinstance(resolved_place, SavedPlace):
            self.config.saved_places[self.config.active_place_index] = resolved_place
            save_config(APP_DIR, self.config)
            if log_trigger:
                self.log(
                    "INFO",
                    f"城市/时区已更新: {resolved_place.name} | {resolved_place.timezone} | "
                    f"{format_utc_offset(resolved_place.utc_offset_seconds)}",
                )
        if error is not None:
            self.pending_auto_test_after_apply = False
            self.last_weather_refresh_at = datetime.now()
            if log_trigger:
                self.log("ERROR", f"天气刷新失败: {error}")
            append_event_log(APP_DIR, "weather_error", str(error))
            self.sync_extension_widgets_from_config()
            self.refresh_dashboard()
            if run_ntp_after_resolve:
                self.sync_ntp_time(trigger_source=trigger_source)
            return
        if snapshot is None:
            self.pending_auto_test_after_apply = False
            if run_ntp_after_resolve:
                self.sync_ntp_time(trigger_source=trigger_source)
            return

        self.last_weather_refresh_at = datetime.now()
        self.last_weather_snapshot = snapshot
        place = self._active_place()
        place.utc_offset_seconds = snapshot.utc_offset_seconds
        self.config.saved_places[self.config.active_place_index] = place
        save_config(APP_DIR, self.config)
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
            self._push_weather_cache_to_board("天气数据已更新")
        if log_trigger:
            _, zone_now, offset_seconds = self._active_place_time_context()
            time_context = (
                f"{place.name} {place.timezone} {format_utc_offset(offset_seconds)} "
                f"当前 {zone_now.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if self.is_connected:
                self.log("INFO", f"天气已刷新并下发: {self.weather_summary_text} | {time_context}")
            else:
                self.log("WARN", f"!!! 已刷新上位机本地天气配置，未下发板端：{self.weather_summary_text} | {time_context}")
        if self.config.auto_day_night:
            self.apply_auto_day_night(self._selected_zone_now().replace(tzinfo=None), force_apply=True)
        if run_ntp_after_resolve:
            self.sync_ntp_time(trigger_source=trigger_source)
        self._maybe_run_pending_auto_test()

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
        self._set_mode_state(expected_mode)
        append_event_log(APP_DIR, "auto_mode", expected_mode)
        self.refresh_dashboard()

    def _resync_mode_to_board_if_conflict(self, board_mode: str, source_text: str) -> bool:
        desired = self._normalize_mode_value(
            self.last_mode or self.runtime_state.mode,
            self.runtime_state.mode,
        )
        if board_mode == desired:
            return False
        now = time.monotonic()
        if now - self.last_mode_resync_monotonic < 2.5:
            return True
        self.last_mode_resync_monotonic = now
        if self.sync_in_progress:
            self.log("INFO", f"检测到模式不一致（{source_text}: {board_mode}，PC: {desired}），等待当前对时写入结束后同步到板端。")
            QtCore.QTimer.singleShot(900, lambda: self._send_pc_mode_to_board(source_text))
            return True
        self.log("WARN", f"检测到模式不一致（{source_text}: {board_mode}，PC: {desired}），已按 PC 设置同步到板端。")
        append_event_log(APP_DIR, "mode_resync", f"{source_text}:{board_mode}->PC:{desired}")
        self._remember_mode_request(desired, "pc_resync")
        self.send_command(f"*SET:MODE {desired}")
        self._set_mode_state(desired)
        self.refresh_dashboard()
        return True

    def _send_pc_mode_to_board(self, source_text: str) -> None:
        if not self.is_connected:
            return
        desired = self._normalize_mode_value(
            self.last_mode or self.runtime_state.mode,
            self.runtime_state.mode,
        )
        if self.sync_in_progress:
            QtCore.QTimer.singleShot(900, lambda: self._send_pc_mode_to_board(source_text))
            return
        if self._serial_auto_quiet_active():
            QtCore.QTimer.singleShot(
                self._serial_auto_quiet_delay_ms(),
                lambda: self._send_pc_mode_to_board(source_text),
            )
            return
        self._remember_mode_request(desired, "pc_resync")
        self._set_mode_state(desired)
        self.send_command(f"*SET:MODE {desired}")
        self.log("INFO", f"{source_text}：已按 PC 当前设置同步昼夜模式 {desired} 到板端。")

    def preview_schedule_ring(self) -> None:
        ring_name = self.ring_names[self.scheduleRingCombo.currentIndex()][0]
        self._play_ring_or_fallback(ring_name, "日程铃声预览")
        self.log("INFO", f"已预览日程铃声: {ring_name}")

    def reset_schedule_form(self) -> None:
        self.scheduleTable.clearSelection()
        self.scheduleTitleEdit.clear()
        self.scheduleBoardLabelEdit.clear()
        self.scheduleTypeCombo.setCurrentIndex(0)
        self._apply_default_schedule_datetime()
        self.scheduleRingCombo.setCurrentIndex(0)
        if hasattr(self, "scheduleVoiceEnabledCheck"):
            self.scheduleVoiceEnabledCheck.setChecked(False)
        self.scheduleVoiceEdit.clear()
        for check in self.scheduleWeekdayChecks:
            check.setChecked(False)
        self._sync_schedule_type_ui()

    def _sync_schedule_type_ui(self) -> None:
        weekly = self.scheduleTypeCombo.currentIndex() == 1
        if hasattr(self, "scheduleDateLabel"):
            self.scheduleDateLabel.setVisible(not weekly)
        if hasattr(self, "scheduleWeekdayLabel"):
            self.scheduleWeekdayLabel.setVisible(weekly)
        if hasattr(self, "scheduleWeekdayHost"):
            self.scheduleWeekdayHost.setVisible(weekly)
        self.scheduleDateEdit.setVisible(not weekly)
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
        board_label = normalize_board_message(self.scheduleBoardLabelEdit.text().strip() or title)
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
        voice_text = (
            self.scheduleVoiceEdit.text().strip()
            if self.scheduleVoiceEnabledCheck.isChecked()
            else ""
        )
        existing = next(
            (item for item in self.schedules if item.item_id == existing_id),
            None,
        )
        return ScheduleItem(
            item_id=existing_id or f"schedule-{int(time.time() * 1000)}",
            title=title,
            board_label=board_label,
            trigger_time=trigger_time,
            schedule_type=schedule_type,
            weekdays=weekdays,
            target_date=target_date if schedule_type == "once" else "",
            ring_type=ring_type,
            enabled=existing.enabled if existing is not None else True,
            voice_text=voice_text or "",
        )

    def save_schedule_item(self) -> None:
        index = self._selected_schedule_index()
        existing_id = self.schedules[index].item_id if index is not None else None
        item = self._collect_schedule_item(existing_id)
        if item.schedule_type == "weekly" and not item.weekdays:
            self.log("WARN", "每周重复提醒至少要勾选一天。")
            return
        if item.enabled and item.schedule_type == "once":
            now = self._selected_zone_now().replace(tzinfo=None)
            if self._next_schedule_time(item, now) is None:
                self.log("WARN", "单次执行提醒的日期和时间已经过去，请选择未来时间或先停用该提醒。")
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
        if hasattr(self, "scheduleVoiceEnabledCheck"):
            self.scheduleVoiceEnabledCheck.setChecked(bool(item.voice_text.strip()))
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
        token = normalize_board_message(item.board_label or item.title)
        ring_name = (item.ring_type or "DEFAULT").strip().upper()
        should_ring = not (self.config.quiet_night_rings and self.last_mode == "NIGHT")
        if self.is_connected:
            self._mark_manual_serial_window(f"提醒 {item.title}", duration_s=3.0)
            commands = ["*SET:DISPLAY ON", f"*SET:MSG {self._board_message_payload(token)}"]
            if should_ring:
                if self.ring_command_supported is False:
                    commands.append(f"*SET:BEEP {self._ring_fallback_duration(ring_name)}")
                else:
                    commands.append(build_set_ring_command(ring_name))
                self.log("INFO", f"日程提醒铃声: {item.title} -> {ring_name}")
            else:
                self.log("INFO", f"日程提醒处于 NIGHT 且已启用夜间抑制，未触发铃声: {item.title}")
            self._send_serial_sequence(commands, gap_ms=220)
        if item.voice_text.strip():
            speak_text(item.voice_text.strip())
        append_event_log(APP_DIR, "schedule_fire", f"{item.title} | {ring_name}")
        self.log("INFO", f"提醒触发: {item.title} | 铃声 {ring_name if should_ring else 'NIGHT_SUPPRESSED'}")
        self.refresh_dashboard()

    def refresh_ports(self) -> None:
        combo = self.ui.portCombo
        line_edit = combo.lineEdit()
        if self.sender() is getattr(self, "port_timer", None):
            view = combo.view()
            user_is_selecting = view is not None and view.isVisible()
            user_is_typing = line_edit is not None and line_edit.hasFocus()
            if user_is_selecting or user_is_typing:
                return

        ports = self._scan_serial_port_names()
        fallback_ports = [] if ports else [f"COM{index}" for index in range(1, 17)]

        current = self._normalize_port_name(combo.currentText())
        connected_port = ""
        if self.serial_port is not None:
            connected_port = self._normalize_port_name(str(getattr(self.serial_port, "port", "") or ""))

        items = [LOCAL_MODE_LABEL]
        for name in ports:
            if name not in items:
                items.append(name)
        for remembered in (current, self.preferred_port_name, connected_port):
            remembered = self._normalize_port_name(remembered)
            if remembered and remembered != LOCAL_MODE_LABEL and remembered not in items:
                items.append(remembered)
        for name in fallback_ports:
            if name not in items:
                items.append(name)

        if (
            current == LOCAL_MODE_LABEL
            and ports
            and not self.manual_port_choice_made
            and not self.local_mode_active
        ):
            target = ports[0]
        elif current:
            target = current
        elif self.preferred_port_name:
            target = self.preferred_port_name
        elif connected_port:
            target = connected_port
        elif ports and not self.manual_port_choice_made and not self.local_mode_active:
            target = ports[0]
        else:
            target = LOCAL_MODE_LABEL

        old_cursor = line_edit.cursorPosition() if line_edit is not None else 0
        old_block = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(items)
            combo.setCurrentText(target if target in items else LOCAL_MODE_LABEL)
        finally:
            combo.blockSignals(old_block)
        if line_edit is not None:
            line_edit.setPlaceholderText("选择或输入 COM5")
            if line_edit.hasFocus():
                line_edit.setCursorPosition(min(old_cursor, len(line_edit.text())))
        if ports:
            combo.setToolTip("选择扫描到的串口；列表暂时为空时可直接输入 COM5。")
        else:
            combo.setToolTip("未发现 COM 口；可从候选项选择 COM5，或直接输入后尝试连接。")

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

        self.local_mode_active = False
        self.status_connection.setText(port_name)
        now_perf = time.perf_counter()
        self.last_serial_rx_monotonic = now_perf
        self.last_tx_monotonic = 0.0
        self.serial_recovery_stage = 0
        self._mark_board_weather_cache_dirty()
        self.poll_timer.start()
        self.ping_timer.start()
        self.board_ready_seen = False
        self.mode_resync_guard_until = time.monotonic() + 4.0
        self.log("INFO", f"已连接 {port_name}")
        self.refresh_dashboard()
        return True

    def connect_port(self) -> bool:
        port_name = self._normalize_port_name(self.ui.portCombo.currentText())
        if not port_name and self.preferred_port_name:
            port_name = self.preferred_port_name
            self.ui.portCombo.setCurrentText(port_name)
        if port_name == LOCAL_MODE_LABEL:
            self.disconnect_port(log_message=False)
            self.local_mode_active = True
            self.status_connection.setText("本地模式")
            self.status_latency.setText("-- ms")
            self._apply_runtime_state_to_ui()
            self.refresh_dashboard()
            self.log("INFO", "已进入本地模式，数字孪生会按影子板端状态持续显示，操作只更新上位机本地配置与模拟状态。")
            return True
        if not port_name:
            self.log("WARN", "没有可连接的 COM 口。")
            return False
        if not self._open_port(port_name):
            return False
        self._schedule_lifecycle_ntp(
            "串口连接成功",
            delay_ms=180,
            fallback_on_fail=True,
            query_after=True,
        )
        QtCore.QTimer.singleShot(
            900,
            lambda: self._push_weather_cache_to_board_if_available("串口连接同步天气缓存"),
        )
        return True

    def connect_and_apply_port(self) -> None:
        if not self.connect_port():
            return
        if self.is_connected:
            self.log("INFO", "连接完成：已自动启动一次 NTP 对时并写入板端，未自动运行联测脚本。")
            return

    def disconnect_port(self, log_message: bool = True) -> None:
        self.poll_timer.stop()
        self.ping_timer.stop()
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        self.last_serial_rx_monotonic = 0.0
        self.last_serial_recovery_monotonic = 0.0
        self.serial_recovery_stage = 0
        self._mark_board_weather_cache_dirty()
        self.manual_ping_log_until = 0.0
        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except Exception:  # noqa: BLE001
                pass
        self.serial_port = None
        self.local_mode_active = False
        self.status_connection.setText("未连接")
        self.status_latency.setText("-- ms")
        self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        if log_message:
            self.log("INFO", "串口已断开。")
        self.refresh_dashboard()

    def query_runtime_state(self) -> None:
        if self._is_local_mode_active():
            self._apply_runtime_state_to_ui()
            self._local_query_notice("运行时状态")
            self.refresh_dashboard()
            return
        if (
            self.sync_in_progress
            or self.ntp_fetch_in_progress
            or self.weather_refresh_in_progress
            or self.test_run_in_progress
            or self._serial_auto_quiet_active()
        ):
            delay_ms = self._serial_auto_quiet_delay_ms(180) if self._serial_auto_quiet_active() else 700
            QtCore.QTimer.singleShot(delay_ms, self.query_runtime_state)
            return
        self.pending_queries.clear()
        self.send_command("*GET:DATE", "DATE")
        self.send_command("*GET:TIME", "TIME")
        self.send_command("*GET:FORMAT", "FORMAT")
        self.send_command("*GET:MODE", "MODE")
        self.send_command("*GET:ALARM", "ALARM")
        self.send_command("*GET:DISPLAY", "DISPLAY")
        self.send_ping()

    def _handle_local_query(self, query: str) -> None:
        self._apply_runtime_state_to_ui()
        if query == "DATE":
            moment = self._get_runtime_datetime()
            self.ui.dateEdit.setDate(QtCore.QDate(moment.year, moment.month, moment.day))
        elif query == "TIME":
            moment = self._get_runtime_datetime()
            self.ui.timeEdit.setTime(QtCore.QTime(moment.hour, moment.minute, moment.second))
        elif query == "FORMAT":
            self.ui.formatCombo.setCurrentText(self.runtime_state.format)
        elif query == "MODE":
            self._set_mode_state(self.runtime_state.mode)
        elif query == "ALARM":
            self._apply_alarm_state_from_text(
                self.runtime_state.alarm_time if self.runtime_state.alarm_enabled else "OFF",
                "本地 ALARM 查询",
            )
        elif query == "DISPLAY":
            self.ui.displayToggleCombo.setCurrentText("ON" if self.runtime_state.display_on else "OFF")
        self._local_query_notice(query)
        self.refresh_dashboard()

    def _display_frame_is_full_hms(self, token: str, dp_mask: int) -> bool:
        text = token_to_text(token, dp_mask).strip()
        parts = text.split(".")
        if len(parts) != 3 or any(len(part) != 2 or not part.isdigit() for part in parts):
            return False
        hour, minute, second = (int(part) for part in parts)
        return 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59

    def _correct_night_display_if_needed(self, token: str, dp_mask: int) -> None:
        if self.last_mode != "NIGHT":
            return
        if not self._display_frame_is_full_hms(token, dp_mask):
            return
        now = time.monotonic()
        if now - self.last_night_display_fix_monotonic < 3.0:
            return
        self.last_night_display_fix_monotonic = now
        self.log("WARN", "检测到 NIGHT 模式仍显示时分秒，已重新向板端同步 NIGHT 显示规则。")
        append_event_log(APP_DIR, "night_display_fix", token_to_text(token, dp_mask))
        QtCore.QTimer.singleShot(120, lambda: self._send_pc_mode_to_board("夜间显示自纠"))

    def _apply_local_command(self, command: str) -> None:
        upper = command.upper()
        moment = self._get_runtime_datetime()
        action = command
        if upper.startswith("*SET:DATE "):
            parts = upper.split()
            try:
                year = int(parts[parts.index("YEAR") + 1])
                month = int(parts[parts.index("MONTH") + 1])
                day = int(parts[parts.index("DATE") + 1])
                moment = moment.replace(year=year, month=month, day=day)
                self._set_runtime_datetime(moment)
                self.ui.dateEdit.setDate(QtCore.QDate(year, month, day))
                action = f"日期 -> {year:04d}-{month:02d}-{day:02d}"
            except Exception:
                action = command
        elif upper.startswith("*SET:TIME "):
            parts = upper.split()
            try:
                hour = int(parts[parts.index("HOUR") + 1])
                minute = int(parts[parts.index("MINUTE") + 1])
                second = int(parts[parts.index("SECOND") + 1])
                moment = moment.replace(hour=hour, minute=minute, second=second)
                self._set_runtime_datetime(moment)
                self.ui.timeEdit.setTime(QtCore.QTime(hour, minute, second))
                action = f"时间 -> {hour:02d}:{minute:02d}:{second:02d}"
            except Exception:
                action = command
        elif upper == "*SET:ALARM OFF":
            self._apply_alarm_state_from_text("OFF", "本地关闭单闹钟")
            action = "关闭单闹钟"
        elif upper.startswith("*SET:ALARM "):
            parts = upper.split()
            try:
                hour = int(parts[parts.index("HOUR") + 1])
                minute = int(parts[parts.index("MINUTE") + 1])
                second = int(parts[parts.index("SECOND") + 1])
                self._apply_alarm_state_from_text(
                    f"{hour:02d}:{minute:02d}:{second:02d}",
                    "本地设置单闹钟",
                )
                action = f"单闹钟 -> {self.runtime_state.alarm_time}"
            except Exception:
                action = command
        elif upper.startswith("*SET:DISPLAY "):
            value = upper.removeprefix("*SET:DISPLAY ").strip()
            self.runtime_state.display_on = value == "ON"
            self.ui.displayToggleCombo.setCurrentText("ON" if self.runtime_state.display_on else "OFF")
            self._save_runtime_state()
            action = f"显示开关 -> {'ON' if self.runtime_state.display_on else 'OFF'}"
        elif upper.startswith("*SET:FORMAT "):
            value = upper.removeprefix("*SET:FORMAT ").strip() or "LEFT"
            self.runtime_state.format = value
            self.ui.formatCombo.setCurrentText(value)
            self._save_runtime_state()
            action = f"FORMAT -> {value}"
        elif upper.startswith("*SET:MODE "):
            value = self._normalize_mode_value(upper.removeprefix("*SET:MODE ").strip(), "DAY")
            self._set_mode_state(value)
            action = f"MODE -> {value}"
        elif upper.startswith("*SET:LED "):
            try:
                self.runtime_state.led_mask = int(command.split()[-1], 16) & 0xFF
                self.ui.ledHexEdit.setText(f"{self.runtime_state.led_mask:02X}")
                self._save_runtime_state()
                action = f"LED 掩码 -> {self.runtime_state.led_mask:02X}"
            except ValueError:
                action = command
        elif upper.startswith("*SET:MSG "):
            text = command[9:].strip()
            self.runtime_state.message_text = text
            self.ui.messageEdit.setText(text)
            self._save_runtime_state()
            self._set_local_display_override(text, self._message_display_duration_s(text))
            action = f"滚动消息 -> {text}"
        elif upper.startswith("*SET:WEATHER DISP "):
            parts = command.split()
            if len(parts) >= 5:
                token = parts[2]
                led_hex = parts[4]
                self.runtime_state.weather_token = token[:8].ljust(8, "_")
                try:
                    self.runtime_state.weather_led_mask = int(led_hex, 16) & 0xFF
                except ValueError:
                    self.runtime_state.weather_led_mask = 0
                self._save_runtime_state()
                action = f"天气短显 -> {self.runtime_state.weather_token}"
        elif upper.startswith("*SET:KEY "):
            key = upper.removeprefix("*SET:KEY ").strip()
            if key == "USER1":
                self.request_user1_time_sync("本地 USER1")
                return
            elif key == "USER2":
                weather_text = (self.runtime_state.weather_token or "").replace("_", " ").strip()
                if not weather_text:
                    weather_text = "NO WX"
                self._set_local_display_override(
                    weather_text,
                    5.0,
                    self.runtime_state.weather_led_mask if self.runtime_state.weather_token else 0,
                )
                action = f"虚拟按键 USER2 -> {weather_text}"
            elif key == "FORMAT":
                self.runtime_state.format = "RIGHT" if self.runtime_state.format == "LEFT" else "LEFT"
                self.ui.formatCombo.setCurrentText(self.runtime_state.format)
                action = f"虚拟按键 FORMAT -> {self.runtime_state.format}"
            elif key == "DISP":
                self._advance_local_display_view_from_disp_key()
                action = f"虚拟按键 DISP -> {self.runtime_state.view_mode}"
            elif key in {"FUNC", "SHIFT", "ADD", "SAVE", "EXT"}:
                self._schedule_single_alarm_query(f"本地虚拟按键 {key}", delay_ms=650)
            self._save_runtime_state()
        elif upper.startswith("*SET:RING "):
            action = f"铃声预览 -> {upper.removeprefix('*SET:RING ').strip()}"
        elif upper.startswith("*SET:BEEP "):
            action = f"蜂鸣预览 -> {upper.removeprefix('*SET:BEEP ').strip()} ms"
        elif upper == "*PING":
            return
        self._apply_runtime_state_to_ui()
        self._set_latest_event(action)
        append_event_log(APP_DIR, "local_apply", action)
        self._local_apply_notice(action)
        self.refresh_dashboard()

    def send_ping(self) -> None:
        if not self.is_connected:
            return
        if self.last_ping_monotonic is not None:
            if time.perf_counter() - self.last_ping_monotonic > 4.5:
                self._check_serial_health()
            return
        if (
            self.sync_in_progress
            or self.ntp_fetch_in_progress
            or self.weather_refresh_in_progress
            or self.test_run_in_progress
            or self._serial_auto_quiet_active()
        ):
            return
        self.last_ping_monotonic = time.perf_counter()
        self.send_command("*PING", heartbeat=True)

    @property
    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def _unsupported_7seg_chars(self, text: str) -> list[str]:
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_.")
        return sorted({ch for ch in text if ch not in allowed})

    def _reject_unsupported_message_command(self, command: str) -> bool:
        upper = command.upper()
        if not upper.startswith("*SET:MSG "):
            return False
        text = command[9:].strip()
        unsupported = self._unsupported_7seg_chars(text)
        if not unsupported:
            return False
        shown = " ".join(repr(ch) for ch in unsupported[:6])
        if len(unsupported) > 6:
            shown += " ..."
        self.log(
            "ERROR",
            f"滚动消息包含 7 段码不支持字符：{shown}；已取消发送，请只使用字母、数字、空格、-、_、.",
        )
        append_event_log(APP_DIR, "message_rejected", f"unsupported chars: {shown}")
        return True

    def send_command(
        self,
        command: str,
        expect: str | None = None,
        *,
        allow_during_sync: bool = False,
        allow_during_test: bool = False,
        heartbeat: bool = False,
    ) -> None:
        cleaned = command.strip()
        if not cleaned:
            return
        if self._reject_unsupported_message_command(cleaned):
            return
        if self.test_run_in_progress and not allow_during_test:
            self.log("WARN", f"自动测试正在占用串口，已暂缓/忽略本次指令: {cleaned}")
            return
        if self.sync_in_progress and not allow_during_sync:
            self.log("WARN", f"对时写入正在进行，已暂缓/忽略本次指令: {cleaned}")
            return
        if not self.is_connected:
            if expect:
                self._handle_local_query(expect)
            else:
                self._apply_local_command(cleaned)
            return
        if expect:
            self.pending_queries.append(expect)
        try:
            with self.serial_io_lock:
                self.serial_port.write((cleaned + "\r\n").encode("ascii", "ignore"))
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"发送失败: {exc}")
            self.disconnect_port(log_message=False)
            return
        self.last_tx_command = cleaned
        self.last_tx_monotonic = time.perf_counter()
        if cleaned == "*PING" and not heartbeat:
            self.manual_ping_log_until = time.monotonic() + 2.5
        if not (heartbeat and cleaned == "*PING" and self.showHeartbeatCheck.isChecked() is False):
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

    def _complete_soft_reset_sync_if_pending(self, evidence: str) -> None:
        if not self.pending_soft_reset_sync:
            return
        if time.monotonic() > self.soft_reset_deadline_monotonic:
            self.pending_soft_reset_sync = False
            self.soft_reset_deadline_monotonic = 0.0
            self.log("WARN", "软 RST 等待板端响应超时，未自动启动本次 NTP。")
            return
        self.pending_soft_reset_sync = False
        self.soft_reset_deadline_monotonic = 0.0
        self.pending_queries.clear()
        self.read_buffer = ""
        self.mode_resync_guard_until = time.monotonic() + 5.0
        self.log("INFO", f"检测到软 RST 已生效（{evidence}），开始排队执行一次 NTP 对时。")
        QtCore.QTimer.singleShot(260, lambda: self._send_pc_mode_to_board("软 RST"))
        self._schedule_lifecycle_ntp(
            "软 RST",
            delay_ms=420,
            fallback_on_fail=True,
            query_after=True,
        )

    def handle_line(self, line: str) -> None:
        raw = line.strip()
        self.last_serial_rx_monotonic = time.perf_counter()
        self.serial_recovery_stage = 0
        if raw.upper() == "S800 CLOCK READY":
            self.log("RX", raw)
            self.board_ready_seen = True
            self.pending_queries.clear()
            self.read_buffer = ""
            self.last_board_display_monotonic = 0.0
            self.mode_resync_guard_until = time.monotonic() + 5.0
            self._mark_board_weather_cache_dirty()
            self._set_latest_event("板端启动，等待真实显示帧")
            if not self.is_connected:
                self._start_boot_mirror_playback()
            if (time.monotonic() - self.last_ready_sync_monotonic) > 2.5:
                self.last_ready_sync_monotonic = time.monotonic()
                self.startup_sync_pending = True
                self.pending_soft_reset_sync = False
                self.soft_reset_deadline_monotonic = 0.0
                self._schedule_lifecycle_ntp(
                    "板端启动",
                    delay_ms=160,
                    fallback_on_fail=True,
                    query_after=False,
                )
                QtCore.QTimer.singleShot(
                    360,
                    lambda: self._send_pc_mode_to_board("板端启动"),
                )
                QtCore.QTimer.singleShot(
                    760,
                    lambda: self._push_weather_cache_to_board_if_available("板端启动同步天气缓存"),
                )
                self.log("INFO", "检测到板端 RESET/启动：数字孪生优先映射板端真实显示帧，后台自动执行 NTP 对时并刷新天气。")
                QtCore.QTimer.singleShot(1200, self._run_startup_sync_after_ready)
            return
        parsed = parse_line(line)
        if not self._should_suppress_rx_log(parsed, line):
            self.log("RX", line)
        if parsed.kind == "event":
            self.handle_event(parsed)
        elif parsed.kind == "pong":
            if self.last_ping_monotonic is not None:
                latency_ms = (time.perf_counter() - self.last_ping_monotonic) * 1000.0
                self.status_latency.setText(f"{latency_ms:.1f} ms")
            self.last_ping_monotonic = None
            self._set_latest_event(f"PONG 延迟 {self.status_latency.text()}")
        elif parsed.kind == "ok":
            if (
                self.pending_soft_reset_sync
                and self.last_tx_command.strip().upper() == "*RST"
                and (time.perf_counter() - self.last_tx_monotonic) < 5.0
            ):
                self._complete_soft_reset_sync_if_pending("OK")
            if self._handle_sync_write_ok():
                return
            self.handle_ok(parsed)
        elif parsed.kind == "error":
            if self._handle_sync_write_error(parsed):
                return
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
            if not self._display_event_token_is_valid(parsed.data):
                self.log("WARN", f"忽略非法 DISPLAY 帧: {parsed.data!r} / {parsed.extra[0]}")
                if self.pending_user2_display_text:
                    self.pending_user2_display_deadline = min(
                        self.pending_user2_display_deadline or time.monotonic(),
                        time.monotonic() + 0.2,
                    )
                    QtCore.QTimer.singleShot(220, self._fallback_user2_weather_display_if_needed)
                return
            self._complete_soft_reset_sync_if_pending("DISP")
            self.boot_mirror_generation += 1
            self.last_board_display_monotonic = time.monotonic()
            display_token = parsed.data.replace("_", " ").replace("~", "_")
            self.twin.set_display_frame(display_token, dp_mask)
            self.last_display_event = (parsed.data, dp_mask)
            self.latest_display_text = f"{parsed.data} / {parsed.extra[0]}"
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
            self._clear_user2_display_pending_if_matched(token_to_text(parsed.data, dp_mask))
            self._correct_night_display_if_needed(parsed.data, dp_mask)
            return

        if parsed.name == "LED":
            try:
                value = int(parsed.data, 16)
            except ValueError:
                return
            self.twin.set_led_byte(value)
            self.last_led_event = value
            self.runtime_state.led_mask = value & 0xFF
            self._save_runtime_state()
            self._update_latest_led_label(value)
            return

        if parsed.name == "MODE":
            if not self._is_valid_mode_value(parsed.data):
                self.log("WARN", f"忽略无效 MODE 事件: {parsed.data or '<empty>'}")
                return
            self._complete_soft_reset_sync_if_pending("MODE")
            next_mode = self._normalize_mode_value(parsed.data, self.last_mode)
            mode_origin = self._consume_mode_request(next_mode)
            if (
                not mode_origin
                and time.monotonic() < self.mode_resync_guard_until
                and self._resync_mode_to_board_if_conflict(next_mode, "RESET/连接恢复")
            ):
                self._set_latest_event(f"RESET MODE {next_mode} 已按 PC 设置回写")
                return
            self._set_mode_state(next_mode)
            append_event_log(APP_DIR, "mode", self.last_mode)
            if self.config.auto_day_night and mode_origin not in {"auto", "test", "pc_resync"}:
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
            self.twin.highlight_key(key)
            append_event_log(APP_DIR, "key", key)
            if key == "USER1":
                self.refresh_dashboard()
                self.request_user1_time_sync("USER1")
                return
            if key == "USER2":
                weather_text = (self.cached_weather_text or self.runtime_state.weather_token or "").replace("_", " ").strip()
                if not weather_text:
                    weather_text = "NO WX"
                self.log("INFO", f"USER2 按键：板端正在显示天气短显 {weather_text}，约 5 秒后回到时钟。")
                self._set_latest_event(f"USER2 -> 天气短显 {weather_text}")
                if (
                    self.is_connected
                    and (self.cached_weather_text or self.runtime_state.weather_token)
                    and time.monotonic() - self.last_user2_replay_monotonic > 2.0
                ):
                    self.last_user2_replay_monotonic = time.monotonic()
                    QtCore.QTimer.singleShot(
                        20,
                        lambda text=weather_text, mask=(self.cached_weather_led_mask or self.runtime_state.weather_led_mask): self._send_user2_weather_message(
                            "板端 USER2 辅助显示",
                            text,
                            mask,
                        ),
                    )
                self.refresh_dashboard()
                return
            if key == "DISP":
                self._advance_local_display_view_from_disp_key()
                self._set_latest_event(f"DISP -> {self.runtime_state.view_mode}")
                self.refresh_dashboard()
                return
            if key in {"FUNC", "SHIFT", "ADD", "SAVE", "EXT"}:
                self._schedule_single_alarm_query(f"板端按键 {key}", delay_ms=650)
            self.refresh_dashboard()
            self._set_latest_event(f"按键事件 -> {key}")
            return

        if parsed.name == "ALARM":
            self._apply_alarm_state_from_text("RINGING", "闹钟事件")
            append_event_log(APP_DIR, "alarm", "RINGING")
            if self.config.voice_enabled:
                speak_text("基础闹钟已触发")
            self.refresh_dashboard()
            self._set_latest_event("闹钟开始响铃")
            return

        if parsed.name == "ALARM_OFF":
            self._apply_alarm_state_from_text("OFF", "闹钟停止事件")
            append_event_log(APP_DIR, "alarm", "OFF")
            self.refresh_dashboard()
            self._set_latest_event("闹钟停止")
            return

        if parsed.name == "EDIT" and parsed.extra:
            self.log("INFO", f"板端保存 {parsed.data}: {parsed.extra[0]}")
            append_event_log(APP_DIR, "edit", f"{parsed.data}: {parsed.extra[0]}")
            if parsed.data.strip().upper() == "ALARM":
                self._apply_alarm_state_from_text(parsed.extra[0], "板端保存 ALARM")
                self._schedule_single_alarm_query("板端保存单次闹钟", delay_ms=260)
            self.refresh_dashboard()
            self._set_latest_event(f"保存 {parsed.data}: {parsed.extra[0]}")

    def _run_startup_sync_after_ready(self) -> None:
        if not self.startup_sync_pending:
            return
        self.startup_sync_pending = False
        self.log("INFO", "板端启动后台流程：NTP 已单独排队，天气刷新只更新天气数据，不再额外触发第二次 NTP。")
        self.refresh_weather_and_push(
            log_trigger=True,
            city_text=self.cityEdit.text().strip() if hasattr(self, "cityEdit") else "",
            resolve_city=True,
            trigger_source="板端启动",
            run_ntp_after_resolve=False,
        )

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
                    moment = self._get_runtime_datetime().replace(year=year, month=month, day=day)
                    self._set_runtime_datetime(moment)
        elif query == "TIME" and data:
            normalized = data.replace(".", ":")
            qtime = QtCore.QTime.fromString(normalized, "HH:mm:ss")
            if qtime.isValid():
                self.ui.timeEdit.setTime(qtime)
                moment = self._get_runtime_datetime().replace(
                    hour=qtime.hour(),
                    minute=qtime.minute(),
                    second=qtime.second(),
                )
                self._set_runtime_datetime(moment)
        elif query == "FORMAT" and data:
            if data in {"LEFT", "RIGHT"}:
                self.ui.formatCombo.setCurrentText(data)
                self.runtime_state.format = data
                self._save_runtime_state()
        elif query == "MODE" and data:
            if data in {"DAY", "NIGHT"}:
                self._set_mode_state(data)
                self.refresh_dashboard()
            else:
                self.log("WARN", f"忽略无效 MODE 查询返回: {data}")
        elif query == "ALARM":
            self._apply_alarm_state_from_text(data or "OFF", "GET:ALARM")
        elif query == "DISPLAY" and data in {"ON", "OFF"}:
            self.ui.displayToggleCombo.setCurrentText(data)
            self.runtime_state.display_on = data == "ON"
            self._save_runtime_state()
        self._refresh_local_twin_frame()
        self.refresh_dashboard()

    def apply_date(self) -> None:
        date = self.ui.dateEdit.date()
        command = (
            f"*SET:DATE YEAR {date.year():04d} "
            f"MONTH {date.month()} DATE {date.day()}"
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
        self.runtime_state.alarm_enabled = True
        self.runtime_state.alarm_time = time_value.toString("HH:mm:ss")
        self.last_alarm = self.runtime_state.alarm_time
        self._save_runtime_state()
        if hasattr(self, "scheduleAlarmTimeEdit"):
            self.scheduleAlarmTimeEdit.setTime(time_value)
        self._refresh_single_alarm_ui()
        command = (
            f"*SET:ALARM HOUR {time_value.hour():02d} "
            f"MINUTE {time_value.minute():02d} SECOND {time_value.second():02d}"
        )
        self.send_command(command)
        self._schedule_single_alarm_query("上位机启用单次闹钟", delay_ms=360)

    def disable_alarm(self) -> None:
        self._apply_alarm_state_from_text("OFF", "上位机关闭单次闹钟")
        self.send_command("*SET:ALARM OFF")
        self._schedule_single_alarm_query("上位机关闭单次闹钟", delay_ms=360)

    def sync_host_time(self) -> None:
        self.sync_ntp_time(trigger_source="按钮")

    def _sync_host_time_step2(self) -> None:
        snapshot = self.sync_snapshot
        if not self.sync_in_progress or self.sync_write_phase != "TIME":
            return
        if snapshot is not None:
            self.send_command(build_set_time_command(snapshot), allow_during_sync=True)
            if not self.is_connected:
                QtCore.QTimer.singleShot(100, self._finish_sync_host_time)

    def _finish_sync_host_time(self) -> None:
        query_after = self.sync_query_after_finish
        was_connected = self.is_connected
        self.sync_in_progress = False
        self.sync_snapshot = None
        self.sync_write_phase = ""
        self.sync_date_retry_done = False
        self.sync_query_after_finish = False
        self._restore_sync_buttons_if_idle()
        if was_connected:
            self.log("INFO", "已完成对时并写入 S800。")
        else:
            self.log("WARN", "!!! 已完成上位机本地时间更新，当前未下发板端。")
        self.refresh_dashboard()
        if query_after:
            QtCore.QTimer.singleShot(500, self.query_runtime_state)
        self._maybe_run_pending_auto_test()
        self._drain_pending_lifecycle_ntp()

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
        self._mark_manual_serial_window("滚动消息", duration_s=2.2)
        self._send_serial_sequence(
            ["*SET:DISPLAY ON", f"*SET:MSG {self._board_message_payload(text)}"],
            gap_ms=180,
        )
        self.ui.messageEdit.setText(text)

    def _extract_key_command(self, command: str) -> str | None:
        parts = command.strip().upper().split()
        if len(parts) == 2 and parts[0] == "*SET:KEY":
            return parts[1]
        return None

    def _send_key_command_safely(self, key_name: str, source_text: str = "按键") -> bool:
        key = key_name.strip().upper()
        if not key:
            return False
        if key == "USER1":
            self.request_user1_time_sync(source_text)
            return True
        if key == "USER2":
            self.log("INFO", f"{source_text} USER2 已走安全天气短显路径，不直接发送原始 USER2 按键。")
            self._trigger_user2_weather_short_display(source_text)
            return True
        now = time.monotonic()
        if self.test_run_in_progress:
            self.log("WARN", f"自动化测试正在占用串口，已忽略人为按键 {key}，避免打断测试状态机。")
            return False
        if self.ntp_fetch_in_progress or self.sync_in_progress or self.weather_refresh_in_progress:
            if now - self.key_command_last_log_monotonic > 1.0:
                self.log("WARN", f"后台对时/天气流程尚未结束，已暂缓按键 {key}，避免串口并发卡死。")
                self.key_command_last_log_monotonic = now
            return False
        if now < self.key_command_guard_until:
            if now - self.key_command_last_log_monotonic > 0.8:
                left_ms = int((self.key_command_guard_until - now) * 1000)
                self.log("WARN", f"按键 {key} 触发过快，已忽略本次输入；请等待约 {left_ms}ms 后再试。")
                self.key_command_last_log_monotonic = now
            return False
        cooldown = 1.0 if key == "FUNC" else 0.75
        if key in {"SHIFT", "ADD", "SAVE"}:
            cooldown = 0.65
        self.key_command_guard_until = now + cooldown
        self.last_key_command_name = key
        self.last_key_command_monotonic = now
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        self._mark_manual_serial_window(f"{source_text}: {key}", duration_s=max(1.4, cooldown + 0.5))
        if key == "DISP":
            self._advance_local_display_view_from_disp_key()
            self._set_latest_event(f"{source_text} DISP -> {self.runtime_state.view_mode}")
        if key in {"FUNC", "SHIFT", "ADD", "SAVE", "EXT"}:
            self._schedule_single_alarm_query(f"{source_text} {key}", delay_ms=1100)
        self.send_command(f"*SET:KEY {key}")
        return True

    def send_virtual_key(self, key_name: str) -> None:
        key = key_name.strip().upper()
        self._send_key_command_safely(key, "虚拟按键")

    def send_raw_command(self) -> None:
        command = self.ui.rawCommandEdit.text().strip()
        if not command:
            return
        key = self._extract_key_command(command)
        if key is not None:
            self._send_key_command_safely(key, "RAW")
            return
        if command.upper() == "*RST" and (self.sync_in_progress or self.test_run_in_progress):
            self.send_command(command)
            return
        self.pending_queries.clear()
        self.last_ping_monotonic = None
        self._mark_manual_serial_window(f"RAW: {command.split()[0]}", duration_s=2.0)
        if command.upper() == "*RST":
            self.pending_soft_reset_sync = True
            self.soft_reset_deadline_monotonic = time.monotonic() + 5.0
            self.log("INFO", "RAW 发送 RST：等待板端复位响应，随后自动执行一次 NTP 对时。")
        self.send_command(command)

    def apply_schedule_alarm(self) -> None:
        time_value = self.scheduleAlarmTimeEdit.time()
        self.ui.alarmTimeEdit.setTime(time_value)
        self.apply_alarm()

    def toggle_schedule_alarm(self) -> None:
        if self.runtime_state.alarm_enabled or self.last_alarm == "RINGING":
            self.disable_alarm()
        else:
            self.apply_schedule_alarm()

    def run_automated_checks(self, full: bool = False) -> None:
        if self.test_run_in_progress:
            return
        if self.ntp_fetch_in_progress or self.sync_in_progress or self.weather_refresh_in_progress:
            self.log("WARN", "对时或天气刷新仍在进行，自动测试稍后再运行，避免抢占串口。")
            self.pending_auto_test_after_apply = True
            self.pending_auto_test_full = self.pending_auto_test_full or bool(full)
            self._maybe_run_pending_auto_test()
            return
        port_name = self.ui.portCombo.currentText().strip()
        host_only = self._is_local_mode_selected() or self._is_local_mode_active()
        if not port_name and not self.is_connected and not host_only:
            self.log("WARN", "没有可测试的 COM 口。")
            return
        self.test_run_in_progress = True
        self.test_run_full = bool(full)
        self.test_saved_auto_day_night = self.config.auto_day_night
        self.test_saved_runtime_state = RuntimeState(**vars(self.runtime_state))
        self.test_saved_section_index = getattr(self.leftSections, "current_index", None)
        cancel_event = threading.Event()
        self.test_cancel_event = cancel_event
        self.test_run_started_at = time.monotonic()
        self.runChecksButton.setEnabled(False)
        if hasattr(self, "runFullChecksButton"):
            self.runFullChecksButton.setEnabled(False)
        if hasattr(self, "abortChecksButton"):
            self.abortChecksButton.setEnabled(True)
        self.testStatusLabel.setText("状态: 运行中")
        active_target = (
            self.serial_port.port
            if self.is_connected and self.serial_port is not None
            else ("本地模式" if host_only else port_name)
        )
        estimated = estimated_duration_seconds(host_only, full=full)
        test_name = "全面联合测试" if full else "快速联合测试"
        if hasattr(self, "status_features"):
            self.status_features.setText(
                f"测试中：{test_name}，正在忽略手动串口操作"
            )
        if hasattr(self, "testEstimateLabel"):
            self.testEstimateLabel.setText(f"预计耗时: 约 {estimated} 秒 | {test_name} | 当前目标: {active_target}")
        self.testOutputText.setPlainText(
            f"预计耗时: 约 {estimated} 秒\n正在对 {active_target} 执行{test_name}...\n"
        )
        if self.is_connected:
            self.poll_timer.stop()
            self.ping_timer.stop()
            self.read_buffer = ""

        def worker() -> None:
            try:
                progress = lambda line: self._emit_signal_safe("test_point_finished", line)
                if self.is_connected and self.serial_port is not None:
                    ok, output = execute_checks_on_open_port(
                        self.serial_port,
                        progress=progress,
                        full=full,
                        initial_mode=self.last_mode,
                        cancel_event=cancel_event,
                    )
                elif host_only:
                    ok, output = execute_host_only_checks(progress=progress, full=full, cancel_event=cancel_event)
                else:
                    ok, output = execute_checks_on_port(port_name, progress=progress, full=full, cancel_event=cancel_event)
            except Exception as exc:  # noqa: BLE001
                output = f"FAIL\n{exc}"
                ok = False
            self._emit_signal_safe("test_run_finished", output.strip(), ok)

        threading.Thread(target=worker, daemon=True).start()

    def abort_automated_checks(self) -> None:
        if not self.test_run_in_progress:
            return
        if self.test_cancel_event is not None:
            self.test_cancel_event.set()
        self.testStatusLabel.setText("状态: 正在中止")
        if hasattr(self, "abortChecksButton"):
            self.abortChecksButton.setEnabled(False)
        self.testOutputText.append("\n用户请求中止：正在等待当前串口步骤结束并恢复测试前状态...")
        self.log("WARN", "用户请求中止自动化测试：将停止后续项目并执行收尾恢复。")

    def _format_test_progress_line(self, line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ""
        if stripped.startswith("[TX] ") or stripped.startswith("[RX] "):
            return ""
        if stripped.startswith("[INFO] 开始测试:"):
            return "开始：" + stripped.split(":", 1)[1].strip()
        if stripped.startswith("[INFO] Rapid key burst"):
            return "开始：高并发按键鲁棒性测试"
        if stripped.startswith("[WARN] "):
            return "WARN：" + stripped[7:].strip()
        if stripped.startswith("[INFO] Restore board settings"):
            return "收尾：恢复测试前 FORMAT/MODE/DISPLAY"
        if stripped.startswith("- "):
            return stripped
        if stripped in {"PASS", "FAIL"}:
            return stripped
        return ""

    def _append_test_output_line(self, line: str) -> None:
        if line.startswith("[TX] "):
            command = line[5:].strip()
            self.last_tx_command = command
            self.last_tx_monotonic = time.perf_counter()
            parts = command.upper().split()
            if len(parts) >= 2 and parts[0] == "*SET:MODE" and parts[1] in {"DAY", "NIGHT"}:
                self._remember_mode_request(parts[1], "test")
            self.log("TX", command)
        elif line.startswith("[RX] "):
            raw = line[5:].strip()
            self.last_serial_rx_monotonic = time.perf_counter()
            self.serial_recovery_stage = 0
            parsed = parse_line(raw)
            if parsed.kind == "event":
                self.handle_event(parsed)
            self.log("RX", raw)
        elif line.startswith("[INFO] "):
            self.log("INFO", line[7:].strip())
        elif line.startswith("[WARN] "):
            self.log("WARN", line[7:].strip())
        display_line = self._format_test_progress_line(line)
        if display_line:
            self.testOutputText.append(display_line)
        cursor = self.testOutputText.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.testOutputText.setTextCursor(cursor)

    def _finish_test_run(self, output: str, ok: bool) -> None:
        was_cancelled = self.test_cancel_event is not None and self.test_cancel_event.is_set()
        self.test_cancel_event = None
        self.test_run_in_progress = False
        self.runChecksButton.setEnabled(True)
        if hasattr(self, "runFullChecksButton"):
            self.runFullChecksButton.setEnabled(True)
        if hasattr(self, "abortChecksButton"):
            self.abortChecksButton.setEnabled(False)
        if self.test_saved_auto_day_night is not None:
            restored = self.test_saved_auto_day_night
            self.config.auto_day_night = restored
            save_config(APP_DIR, self.config)
            if hasattr(self, "autoDayNightCheck"):
                old_block = self.autoDayNightCheck.blockSignals(True)
                self.autoDayNightCheck.setChecked(restored)
                self.autoDayNightCheck.blockSignals(old_block)
            self.test_saved_auto_day_night = None
        if self.test_saved_runtime_state is not None:
            self.runtime_state = self.test_saved_runtime_state
            self.test_saved_runtime_state = None
            self._set_mode_state(self.runtime_state.mode, save=False, update_combo=True, update_theme=True)
            self._apply_runtime_state_to_ui()
            self._save_runtime_state()
        self.last_test_ok = ok
        self.last_test_summary = "CANCELLED" if was_cancelled else ("PASS" if ok else "FAIL")
        self.testStatusLabel.setText(
            "状态: 已中止" if was_cancelled else f"状态: {'通过' if ok else '失败'}"
        )
        if self.test_run_started_at:
            elapsed = time.monotonic() - self.test_run_started_at
            if hasattr(self, "testEstimateLabel"):
                self.testEstimateLabel.setText(f"预计耗时已结束 | 实际耗时: {elapsed:.1f} 秒")
            self.test_run_started_at = 0.0
        self.testOutputText.append("\n--- 汇总 ---")
        self.testOutputText.append(output or ("PASS" if ok else "FAIL"))
        if (not ok) and (
            "timeout" in output.lower()
            or "PONG" in output.upper()
            or "卡死" in output
            or "无响应" in output
        ):
            stuck_tip = "疑似硬件端状态机卡死或串口无响应：请手动 RESET 板卡，等待 READY 后再运行测试。"
            self.testOutputText.append(stuck_tip)
            self.log("ERROR", stuck_tip)
        cursor = self.testOutputText.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.testOutputText.setTextCursor(cursor)
        if self.is_connected:
            self.poll_timer.start()
            self.ping_timer.start()
            self.query_runtime_state()
        if self.test_saved_section_index is not None:
            try:
                self.leftSections.select_index(self.test_saved_section_index)
            except Exception:  # noqa: BLE001
                pass
            self.test_saved_section_index = None
        append_event_log(APP_DIR, "test_run", self.last_test_summary)
        self.refresh_dashboard()
        if hasattr(self, "status_features"):
            self._restore_footer_features()
        self.log("INFO" if ok else "WARN", f"联合测试完成: {self.last_test_summary}")
        run_full_followup = self.test_run_full and ok
        self.test_run_full = False
        if run_full_followup:
            self.testOutputText.append("\n[INFO] 全面测试追加：异步触发一次城市/天气/NTP 一键流程，结果请看日志。")
            QtCore.QTimer.singleShot(
                600,
                lambda: self.sync_weather_and_apply(
                    trigger_source="全面自动测试",
                    run_tests=False,
                ),
            )

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
            self.autoModeNoticeLabel.clear()
            self.autoModeNoticeLabel.setVisible(False)
        if hasattr(self, "autoDayNightCheck"):
            old_block = self.autoDayNightCheck.blockSignals(True)
            self.autoDayNightCheck.setChecked(False)
            self.autoDayNightCheck.blockSignals(old_block)
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
        if error_text == "STATE":
            self.local_display_override_text = ""
            self.local_display_override_started = 0.0
            self.local_display_override_until = 0.0
            self.local_display_override_led_mask = None
            message = "板端显示状态机超时，已自动清退临时显示并恢复时钟。"
            append_event_log(APP_DIR, "state_recover", message)
            self.log("WARN", message)
            if self.is_connected:
                QtCore.QTimer.singleShot(300, self.query_runtime_state)
            self.refresh_dashboard()
            return True
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
            if time.monotonic() < self.manual_ping_log_until:
                return False
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
    _set_windows_app_user_model_id()
    app = QtWidgets.QApplication(sys.argv)
    icon_path = ICON_PATH if ICON_PATH.exists() else LOGO_PATH
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = MainWindow()
    window.showMaximized()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
