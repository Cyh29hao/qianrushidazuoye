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

APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
QT_RUNTIME = configure_qt_runtime(APP_DIR)
APP_VERSION = "v2.0"
GITHUB_URL = "https://github.com/Cyh29hao"
LOGO_PATH = BUNDLE_DIR / "assets" / "clock_logo.svg"
LOCAL_MODE_LABEL = "不使用串口"

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
)
from run_extension_checks import (
    estimated_duration_seconds,
    execute_checks_on_open_port,
    execute_checks_on_port,
    execute_host_only_checks,
)
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
    ntp_sync_finished = QtCore.pyqtSignal(object, object, str)
    test_point_finished = QtCore.pyqtSignal(str)
    test_run_finished = QtCore.pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        ensure_storage(APP_DIR)
        self.config: AppConfig = load_config(APP_DIR)
        self.runtime_state: RuntimeState = load_runtime_state(APP_DIR)
        self.schedules: list[ScheduleItem] = load_schedules(APP_DIR)
        self.log_dir = APP_DIR / "logs"
        self.setWindowTitle(f"智能联网时钟系统 - PC 上位机 {APP_VERSION}")
        if LOGO_PATH.exists():
            self.setWindowIcon(QtGui.QIcon(str(LOGO_PATH)))

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
        self.latest_display_text = "--"
        self.latest_led_text = "--"
        self.latest_event_text = "等待数据"
        self.max_log_blocks = 400
        self.sync_in_progress = False
        self.ntp_fetch_in_progress = False
        self.sync_snapshot: datetime | None = None
        self.runtime_shadow_base_iso = ""
        self.runtime_shadow_base_datetime: datetime | None = None
        self.runtime_shadow_base_monotonic = 0.0
        self.weather_refresh_in_progress = False
        self.last_weather_refresh_at: datetime | None = None
        self.last_mode_auto_applied = ""
        self.last_tx_command = ""
        self.last_tx_monotonic = 0.0
        self.ring_command_supported: bool | None = None
        self.last_test_summary = "未运行"
        self.last_test_ok = False
        self.test_run_in_progress = False
        self.test_run_started_at = 0.0
        self.pending_auto_test_after_apply = False
        self.last_apply_monotonic = 0.0
        self.last_ready_sync_monotonic = 0.0
        self.last_mode_expected = ""
        self.pending_mode_origin = ""
        self.pending_mode_value = ""
        self.pending_mode_deadline = 0.0
        self.board_ready_seen = False
        self.local_display_override_text = ""
        self.local_display_override_started = 0.0
        self.local_display_override_until = 0.0
        self.local_display_override_led_mask: int | None = None
        self.local_view_scroll_key = ""
        self.local_view_scroll_started = 0.0
        self.boot_mirror_generation = 0
        self.startup_sync_pending = False
        self.syncing_extension_widgets = False
        self.preferred_port_name = ""
        self.manual_port_choice_made = False
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
        self.log("INFO", "PC 上位机已启动，等待连接 S800。")

    def _build_statusbar(self) -> None:
        self.status_project = QtWidgets.QLabel("智能联网时钟系统")
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
        self.status_connection = QtWidgets.QLabel("连接: 未连接")
        self.status_mode = QtWidgets.QLabel("MODE: DAY")
        self.status_location = QtWidgets.QLabel("LOCATION: 上海")
        self.status_local_time = QtWidgets.QLabel("本地时间: --:--:--")
        self.status_latency = QtWidgets.QLabel("延迟: -- ms")
        self.status_version = QtWidgets.QLabel(APP_VERSION)
        self.status_developer = QtWidgets.QLabel("开发者: Cyh29hao")
        self.status_clear_button = QtWidgets.QToolButton(self)
        self.status_clear_button.setText("清空日志")
        self.status_export_button = QtWidgets.QToolButton(self)
        self.status_export_button.setText("导出日志")
        self.status_github_button = QtWidgets.QToolButton(self)
        self.status_github_button.setText("GitHub")
        self.status_github_button.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_URL))
        )
        self.status_clear_button.clicked.connect(self.ui.logTextEdit.clear)
        self.status_export_button.clicked.connect(self.export_log)
        self.ui.statusbar.addWidget(self.status_project_icon)
        self.ui.statusbar.addWidget(self.status_project, 1)
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

    def _set_runtime_datetime(self, moment: datetime) -> None:
        clean = moment.replace(microsecond=0)
        self.runtime_state.board_datetime_iso = clean.isoformat(sep=" ")
        self.runtime_shadow_base_iso = self.runtime_state.board_datetime_iso
        self.runtime_shadow_base_datetime = clean
        self.runtime_shadow_base_monotonic = time.monotonic()
        self.runtime_state.shadow_saved_at_utc_iso = datetime.utcnow().replace(
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
                        elapsed = max(
                            0,
                            int((datetime.utcnow() - saved_at).total_seconds()),
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
        token = "".join("_" if ch == " " else ch for ch in chars[:8])
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
            return 5.0
        return 8.0

    def _scroll_leg_count(self, limit: int) -> int:
        if limit <= 0:
            return 0
        if limit <= 1:
            return 1
        if limit <= 5:
            return 3
        if limit <= 10:
            return 2
        return 1

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
        duration = min(10.0, max(5.0, duration_s))
        max_step = self._scroll_max_step(limit)
        interval = duration / max(1, max_step + 1)
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
            return "________", 0
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
        token, dp_mask = self._current_local_display_frame()
        led_mask = self._current_local_led_mask()
        self.twin.set_display_frame(token, dp_mask)
        self.twin.set_led_byte(led_mask)
        self.latest_display_text = f"{token} / {dp_mask:02X}"
        self.latest_led_text = f"{led_mask:02X}"
        if hasattr(self, "latestDisplayLabel"):
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        if hasattr(self, "latestLedLabel"):
            self.latestLedLabel.setText(f"最新 LED: {self.latest_led_text}")

    def _start_boot_mirror_playback(self) -> None:
        self.boot_mirror_generation += 1
        generation = self.boot_mirror_generation
        frames = [
            (0, "88888888", 0xFF, 0xFF),
            (1000, "________", 0x00, 0x00),
            (2000, "31910102", 0x00, 0xFF),
            (3000, "________", 0x00, 0x00),
            (4000, "CHENYH__", 0x00, 0xFF),
            (5000, "________", 0x00, 0x00),
            (6000, "V2_0____", 0x04, 0xFF),
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
        self.twin.set_display_frame(token, dp_mask)
        self.twin.set_led_byte(led_mask)
        self.latest_display_text = f"{token} / {dp_mask:02X}"
        self.latest_led_text = f"{led_mask:02X}"
        if hasattr(self, "latestDisplayLabel"):
            self.latestDisplayLabel.setText(f"最新显示: {self.latest_display_text}")
        if hasattr(self, "latestLedLabel"):
            self.latestLedLabel.setText(f"最新 LED: {self.latest_led_text}")

    def _finish_boot_mirror_playback(self, generation: int) -> None:
        if generation != self.boot_mirror_generation:
            return
        self._refresh_local_twin_frame()

    def _local_apply_notice(self, action: str) -> None:
        self.log("WARN", f"!!! 仅更新上位机本地配置/模拟状态，未下发板端：{action}")

    def _local_query_notice(self, action: str) -> None:
        self.log("WARN", f"!!! 当前为本地模式，显示的是上位机保存状态，并非板端实时返回：{action}")

    def _apply_runtime_state_to_ui(self) -> None:
        self.ui.displayToggleCombo.setCurrentText("ON" if self.runtime_state.display_on else "OFF")
        self.ui.formatCombo.setCurrentText(self.runtime_state.format)
        self.ui.modeCombo.setCurrentText(self.runtime_state.mode)
        self.last_mode = self.runtime_state.mode
        self.last_alarm = self.runtime_state.alarm_time if self.runtime_state.alarm_enabled else "OFF"
        alarm_time = QtCore.QTime.fromString(self.runtime_state.alarm_time, "HH:mm:ss")
        if alarm_time.isValid():
            self.ui.alarmTimeEdit.setTime(alarm_time)
            if hasattr(self, "scheduleAlarmTimeEdit"):
                self.scheduleAlarmTimeEdit.setTime(alarm_time)
        board_time = self._get_runtime_datetime()
        self.ui.dateEdit.setDate(QtCore.QDate(board_time.year, board_time.month, board_time.day))
        self.ui.timeEdit.setTime(QtCore.QTime(board_time.hour, board_time.minute, board_time.second))
        self.ui.ledHexEdit.setText(f"{self.runtime_state.led_mask:02X}")
        self.ui.messageEdit.setText(self.runtime_state.message_text)
        self._refresh_local_twin_frame()
        self.status_mode.setText(f"MODE: {self.runtime_state.mode}")
        self._refresh_theme_from_mode()
        self._refresh_single_alarm_ui()

    def _selected_zone_now(self, utc_moment: datetime | None = None) -> datetime:
        place = self._active_place()
        return timezone_now(
            place.timezone,
            utc_moment,
            fallback_offset_seconds=place.utc_offset_seconds,
        )

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
        if now.hour < 11:
            period = "早上"
        elif now.hour < 18:
            period = "中午"
        else:
            period = "晚上"
        name = (self.config.user_name or "用户").strip() or "用户"
        return f"{period}好，{name}！"

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
            QWidget#centralwidget {{
                background: {palette['background']};
            }}
            QGroupBox {{
                background: {palette['group_bg']};
                border: 1px solid {palette['group_border']};
                border-radius: 8px;
                margin-top: 26px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                top: 0px;
                padding: 0 8px 2px 8px;
                background: {palette['background']};
                color: {palette['title']};
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
                padding-right: 28px;
            }}
            QComboBox::drop-down, QDateEdit::drop-down, QTimeEdit::drop-down {{
                background: {palette['input_bg']};
                border-left: 1px solid {palette['input_border']};
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 28px;
            }}
            QComboBox::down-arrow, QDateEdit::down-arrow, QTimeEdit::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {palette['text']};
                margin-right: 8px;
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
                image: none;
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid {palette['text']};
            }}
            QAbstractSpinBox::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {palette['text']};
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
        list_style = (
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"selection-background-color: {palette['button']};"
            f"selection-color: white;"
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
            f"padding: 6px 34px 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QComboBox::drop-down {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"border-top-right-radius: 8px;"
            f"border-bottom-right-radius: 8px;"
            f"subcontrol-origin: border;"
            f"subcontrol-position: top right;"
            f"width: 28px;"
            f"}}"
            f"QComboBox::down-arrow {{"
            f"image: none;"
            f"width: 0px;"
            f"height: 0px;"
            f"border-left: 5px solid transparent;"
            f"border-right: 5px solid transparent;"
            f"border-top: 6px solid {palette['text']};"
            f"margin-right: 8px;"
            f"}}"
            f"QComboBox QAbstractItemView {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"selection-background-color: {palette['button']};"
            f"selection-color: white;"
            f"outline: none;"
            f"}}"
            f"{scrollbar_style}"
        )
        state_combo_style = (
            f"QComboBox {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QComboBox::drop-down {{ width: 0px; border: none; }}"
            f"QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}"
            f"QComboBox QAbstractItemView {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"selection-background-color: {palette['button']};"
            f"selection-color: white;"
            f"outline: none;"
            f"}}"
        )
        spin_style = (
            f"QDateEdit, QTimeEdit, QSpinBox {{"
            f"background: {palette['input_bg']};"
            f"color: {palette['text']};"
            f"border: 1px solid {palette['input_border']};"
            f"border-radius: 8px;"
            f"padding: 6px 34px 6px 10px;"
            f"min-height: 32px;"
            f"}}"
            f"QDateEdit::drop-down, QTimeEdit::drop-down {{"
            f"background: {palette['input_bg']};"
            f"border-left: 1px solid {palette['input_border']};"
            f"border-top-right-radius: 8px;"
            f"border-bottom-right-radius: 8px;"
            f"subcontrol-origin: border;"
            f"subcontrol-position: top right;"
            f"width: 28px;"
            f"}}"
            f"QDateEdit::down-arrow, QTimeEdit::down-arrow {{"
            f"image: none;"
            f"width: 0px;"
            f"height: 0px;"
            f"border-left: 5px solid transparent;"
            f"border-right: 5px solid transparent;"
            f"border-top: 6px solid {palette['text']};"
            f"margin-right: 8px;"
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
            f"image: none;"
            f"width: 0px;"
            f"height: 0px;"
            f"border-left: 4px solid transparent;"
            f"border-right: 4px solid transparent;"
            f"border-bottom: 5px solid {palette['text']};"
            f"}}"
            f"QAbstractSpinBox::down-arrow {{"
            f"image: none;"
            f"width: 0px;"
            f"height: 0px;"
            f"border-left: 4px solid transparent;"
            f"border-right: 4px solid transparent;"
            f"border-top: 5px solid {palette['text']};"
            f"}}"
            f"{scrollbar_style}"
        )
        header_style = (
            f"background: {palette['chip_bg']};"
            f"color: {palette['chip_text']};"
            f"border: 1px solid {palette['input_border']};"
        )
        page_style = f"background: {palette['background']}; color: {palette['text']};"
        viewport_style = f"background: {palette['input_bg']}; color: {palette['text']};"
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
                f"padding: 7px 14px;"
                f"min-height: 34px;"
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
            f"font-size: 11px;"
            f"font-weight: 700;"
        )
        card_value_style = (
            f"color: {palette['title']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 20px;"
            f"font-weight: 800;"
        )
        card_sub_style = (
            f"color: {palette['chip_text']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 11px;"
        )
        card_greeting_style = (
            f"color: {palette['title']};"
            f"background: transparent;"
            f"border: none;"
            f"font-size: 18px;"
            f"font-weight: 800;"
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
            f"background: {palette['button']};"
            f"border: 1px solid {palette['button_hover']};"
            f"}}"
            f"QCheckBox::indicator:disabled {{"
            f"background: {palette['button_disabled']};"
            f"border: 1px solid {palette['group_border']};"
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

        for widget in self.findChildren(QtWidgets.QWidget, "sectionPageHost"):
            widget.setStyleSheet(page_style)
        for area in self.findChildren(QtWidgets.QScrollArea):
            area.setStyleSheet(scroll_page_style)
            area.viewport().setStyleSheet(page_style)
        for button_type in (QtWidgets.QPushButton, QtWidgets.QToolButton):
            for button in self.findChildren(button_type):
                if button.objectName() in {"accordionHeader", "accordionOptionButton"}:
                    continue
                button.setStyleSheet(make_button_style(button_type.__name__))
                button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        for widget in self.findChildren(QtWidgets.QLineEdit):
            widget.setStyleSheet(field_style)
        for widget in self.findChildren(QtWidgets.QComboBox):
            widget.setStyleSheet(
                state_combo_style if widget.property("stateField") else combo_style
            )
            if widget is getattr(self.ui, "portCombo", None) and widget.lineEdit() is not None:
                widget.lineEdit().setStyleSheet(
                    field_line_style
                )
            if widget.view() is not None:
                widget.view().setStyleSheet(list_style)
                widget.view().viewport().setStyleSheet(viewport_style)
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
            widget.setStyleSheet(list_style)
            if hasattr(widget, "viewport") and widget.viewport() is not None:
                widget.viewport().setStyleSheet(viewport_style)
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

    def _normalize_groupbox_layouts(self) -> None:
        for group in self.findChildren(QtWidgets.QGroupBox):
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

        self.ui.connectButton.setText("连接")
        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("DAY/NIGHT 切换")
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
        left_panel.setFixedWidth(600)
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
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
        right_panel.setMinimumWidth(0)
        right_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)
        right_layout.addWidget(self.ui.twinGroup, 0, QtCore.Qt.AlignTop)
        right_layout.addWidget(self.ui.logGroup, 1)

        self.ui.connectionGroup.setMinimumHeight(132)
        self.ui.clockGroup.setMinimumHeight(214)
        self.ui.displayGroup.setMinimumHeight(368)
        self.ui.demoGroup.setMinimumHeight(238)

        screen = QtWidgets.QApplication.primaryScreen()
        available_height = (
            screen.availableGeometry().height() if screen is not None else 900
        )
        required_twin_height = max(
            self.twin.sizeHint().height() + 46,
            int(available_height * 0.27),
        )
        self.ui.twinGroup.setTitle("")
        self.ui.twinGroup.setFixedHeight(required_twin_height)
        self.ui.twinGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        self.ui.logGroup.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        self.ui.horizontalLayout.addWidget(left_panel)
        self.ui.horizontalLayout.addWidget(right_panel, 1)

        log_layout = self.ui.logGroup.layout()
        if log_layout is not None and not hasattr(self, "latestDisplayLabel"):
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
        log_line_height = QtGui.QFontMetrics(self.ui.logTextEdit.font()).lineSpacing()
        log_text_height = log_line_height * 9 + 32
        self.ui.logTextEdit.setFixedHeight(log_text_height)
        self.ui.logTextEdit.document().setMaximumBlockCount(self.max_log_blocks)
        self.ui.clearLogButton.setMinimumHeight(28)
        self.ui.exportLogButton.setMinimumHeight(28)
        self.ui.horizontalLayout_6.setSpacing(10)
        self.ui.horizontalLayout_6.setStretch(0, 1)
        self.ui.horizontalLayout_6.setStretch(1, 1)
        summary_height = (
            self.logSummaryWidget.sizeHint().height()
            if hasattr(self, "logSummaryWidget")
            else 92
        )
        self.ui.logGroup.setFixedHeight(summary_height + log_text_height + 56)

        for button in self.findChildren(QtWidgets.QPushButton):
            button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            button.setMinimumHeight(36)
            button.setMinimumWidth(104)

        for combo in self.findChildren(QtWidgets.QComboBox):
            self._install_wheel_guard(combo)
            combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(6)
        self.ui.portCombo.setMinimumContentsLength(12)
        self.ui.portCombo.setMinimumWidth(180)
        self.ui.portCombo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for spinbox in self.findChildren(QtWidgets.QAbstractSpinBox):
            self._install_wheel_guard(spinbox)

        self.ui.messageEdit.setClearButtonEnabled(True)
        self.ui.rawCommandEdit.setClearButtonEnabled(True)
        self.ui.ledHexEdit.setClearButtonEnabled(True)
        for spinbox in (self.ui.dateEdit, self.ui.timeEdit, self.ui.alarmTimeEdit):
            line_edit = spinbox.findChild(QtWidgets.QLineEdit)
            if line_edit is not None:
                line_edit.setClearButtonEnabled(False)

        self.ui.gridLayout.setColumnMinimumWidth(0, 86)
        self.ui.gridLayout.setColumnMinimumWidth(2, 114)
        self.ui.gridLayout.setColumnMinimumWidth(3, 86)
        self.ui.gridLayout.setColumnStretch(0, 0)
        self.ui.gridLayout.setColumnStretch(1, 4)
        self.ui.gridLayout.setColumnStretch(2, 0)
        self.ui.gridLayout.setColumnStretch(3, 0)
        self.ui.gridLayout.setHorizontalSpacing(12)
        self.ui.gridLayout.setVerticalSpacing(12)
        self.ui.gridLayout.setContentsMargins(12, 28, 12, 12)
        for row in range(4):
            self.ui.gridLayout.setRowMinimumHeight(row, 44)
        self.ui.gridLayout_2.setColumnMinimumWidth(0, 86)
        self.ui.gridLayout_2.setColumnMinimumWidth(2, 116)
        self.ui.gridLayout_2.setColumnMinimumWidth(3, 104)
        self.ui.gridLayout_2.setColumnStretch(0, 0)
        self.ui.gridLayout_2.setColumnStretch(1, 4)
        self.ui.gridLayout_2.setColumnStretch(2, 0)
        self.ui.gridLayout_2.setColumnStretch(3, 0)
        self.ui.gridLayout_2.setHorizontalSpacing(12)
        self.ui.gridLayout_2.setVerticalSpacing(12)
        self.ui.gridLayout_2.setContentsMargins(12, 28, 12, 12)
        for row in range(7):
            self.ui.gridLayout_2.setRowMinimumHeight(row, 44)
        self.ui.verticalLayout_2.setSpacing(10)
        self.ui.verticalLayout_2.setContentsMargins(12, 28, 12, 12)
        self.ui.verticalLayout_3.setSpacing(8)
        self.ui.verticalLayout_3.setContentsMargins(12, 28, 12, 12)
        self.ui.verticalLayout_4.setSpacing(4)
        self.ui.verticalLayout_4.setContentsMargins(12, 2, 12, 6)
        self.ui.verticalLayout_5.setSpacing(8)
        self.ui.verticalLayout_5.setContentsMargins(12, 20, 12, 8)

        self.ui.connectButton.setText("连接")
        self.ui.syncNowButton.setText("一键对时并写入")
        self.ui.applyDisplayButton.setText("切换并应用")
        self.ui.applyFormatButton.setText("切换并应用")
        self.ui.applyModeButton.setText("DAY/NIGHT 切换")
        self.ui.sendLedButton.setText("设置 LED")
        self.ui.sendPresetButton.setText("发送预设")
        self.ui.mixedCaseDemoButton.setText("混合大小写")
        self._normalize_groupbox_layouts()

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
        self.scheduleAlarmTimeEdit.setAlignment(QtCore.Qt.AlignCenter)
        self.scheduleApplyAlarmButton = QtWidgets.QPushButton("启用", group)
        self.scheduleDisableAlarmButton = QtWidgets.QPushButton("关闭", group)
        self.scheduleDisableAlarmButton.setVisible(False)
        self.scheduleQueryAlarmButton = QtWidgets.QPushButton("查询", group)
        self.scheduleQueryAlarmButton.setMaximumWidth(96)
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
        layout.addWidget(button_row, 2, 1, 1, 2)
        layout.addWidget(self.scheduleAlarmHintLabel, 3, 0, 1, 3)
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
        self.scheduleTypeCombo.addItems(["单次执行", "每周重复"])
        self.scheduleDateEdit = QtWidgets.QDateEdit(schedule_group)
        self.scheduleDateEdit.setCalendarPopup(True)
        self.scheduleDateEdit.setDate(QtCore.QDate.currentDate())
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
        self.scheduleDateLabel = QtWidgets.QLabel("执行日期", schedule_group)
        self.scheduleWeekdayLabel = QtWidgets.QLabel("每周", schedule_group)
        self.scheduleWeekdayHost = weekday_host

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
        form.addWidget(self.scheduleRingCombo, 6, 1)
        form.addWidget(QtWidgets.QLabel("语音"), 7, 0)
        form.addWidget(self.scheduleVoiceEdit, 7, 1)

        button_row = QtWidgets.QWidget(schedule_group)
        button_layout = QtWidgets.QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.scheduleSaveButton)

        form.addWidget(button_row, 8, 1)
        form.addWidget(self.scheduleDeleteButton, 9, 1)
        schedule_layout.addLayout(form)
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
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_label = QtWidgets.QLabel(title, card)
        title_label.setProperty("dashboardTitle", True)
        value_label = QtWidgets.QLabel(value, card)
        value_label.setProperty("dashboardValue", True)
        sub_label = QtWidgets.QLabel(subtext, card)
        sub_label.setProperty("dashboardSub", True)
        for label in (title_label, value_label, sub_label):
            label.setWordWrap(True)
            label.setStyleSheet("")
        value_label.setMinimumHeight(28)

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(sub_label)
        layout.addStretch(1)
        return card, value_label, sub_label

    def _build_dashboard_group(self, parent: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        dashboard_group = QtWidgets.QGroupBox("数据看板", parent)
        dashboard_layout = QtWidgets.QVBoxLayout(dashboard_group)
        dashboard_layout.setContentsMargins(14, 30, 14, 14)
        dashboard_layout.setSpacing(12)

        self.greetingLabel = QtWidgets.QLabel("早上好，用户！", dashboard_group)
        self.greetingLabel.setProperty("dashboardGreeting", True)
        self.greetingLabel.setWordWrap(True)
        self.greetingLabel.setStyleSheet("")
        dashboard_layout.addWidget(self.greetingLabel)

        card_grid = QtWidgets.QGridLayout()
        card_grid.setHorizontalSpacing(10)
        card_grid.setVerticalSpacing(10)
        card_grid.setColumnStretch(0, 1)
        card_grid.setColumnStretch(1, 1)

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
        card_grid.addWidget(weather_card, 1, 0)
        card_grid.addWidget(mode_card, 1, 1)
        card_grid.addWidget(schedule_card, 2, 0)
        card_grid.addWidget(system_card, 2, 1)
        dashboard_layout.addLayout(card_grid)
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
        self.testEstimateLabel = QtWidgets.QLabel(
            f"预计耗时: 串口约 {estimated_duration_seconds(False)} 秒，本地模式约 {estimated_duration_seconds(True)} 秒",
            test_group,
        )
        self.testEstimateLabel.setProperty("class", "infoChip")
        self.testEstimateLabel.setStyleSheet("")
        self.testExplainLabel = QtWidgets.QLabel(
            "覆盖 PING、SET/GET、日期时间写入、模式切换、天气协议、铃声协议与关键快捷键。",
            test_group,
        )
        self.testExplainLabel.setWordWrap(True)
        self.testExplainLabel.setProperty("class", "infoChip")
        self.testExplainLabel.setStyleSheet("")
        self.boardShortcutLabel = QtWidgets.QLabel(
            "板载快捷：USER1 短按请求 PC 对时，长按切日夜；DISP 长按关显示并关 LED；EXT 用于退出/取消当前编辑或临时显示。",
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
        test_layout.addWidget(self.testEstimateLabel)
        test_layout.addWidget(self.testExplainLabel)
        test_layout.addWidget(self.boardShortcutLabel)
        test_layout.addWidget(self.testOutputText)

        outer.addWidget(test_group)
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

        preset_items = [
            "*SET:DATE YEAR 2026 MONTH 06 DATE 02",
            "*SET:TIME HOUR 12 MINUTE 30 SECOND 45",
            "*SET:ALARM HOUR 07 MINUTE 30 SECOND 00",
            "*SET:MSG Hello Clock",
            "*SET:DISPLAY OFF",
            "*SET:DISPLAY ON",
        ]
        self.ui.presetCombo.addItems(preset_items)
        self._apply_runtime_state_to_ui()

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
        self.usernameEdit = QtWidgets.QLineEdit(self.ui.displayGroup)
        self.usernameEdit.setPlaceholderText("默认 用户")
        self.usernameSaveButton = QtWidgets.QPushButton("确认用户名", self.ui.displayGroup)
        self.ui.gridLayout_2.addWidget(QtWidgets.QLabel("用户名", self.ui.displayGroup), 6, 0)
        self.ui.gridLayout_2.addWidget(self.usernameEdit, 6, 1)
        self.ui.gridLayout_2.addWidget(self.usernameSaveButton, 6, 2)
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
            if hasattr(self, "autoRunTestsCheck"):
                old_block = self.autoRunTestsCheck.blockSignals(True)
                self.autoRunTestsCheck.setChecked(self.config.auto_run_tests_on_start)
                self.autoRunTestsCheck.blockSignals(old_block)
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
        self.status_connection.setText(f"连接: {connection_text}")
        self.status_mode.setText(f"MODE: {self.last_mode}")
        self.status_location.setText(f"LOCATION: {place.name}")
        self.status_local_time.setText(f"本地时间: {datetime.now().strftime('%H:%M:%S')}")
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
        if self.lookupCityButton is not None:
            self.lookupCityButton.clicked.connect(self.lookup_city)
        if self.saveExtensionConfigButton is not None:
            self.saveExtensionConfigButton.clicked.connect(self.save_extension_config)
        self.syncWeatherApplyButton.clicked.connect(
            lambda: self.sync_weather_and_apply(trigger_source="按钮", run_tests=False)
        )
        self.autoDayNightCheck.toggled.connect(self.set_auto_day_night_enabled)
        self.usernameSaveButton.clicked.connect(self.save_user_name)
        self.ringPreviewButton.clicked.connect(self.preview_ring)
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
        self.scheduleTypeCombo.currentIndexChanged.connect(self._sync_schedule_type_ui)
        self.runChecksButton.clicked.connect(self.run_automated_checks)
        self.autoRunTestsCheck.toggled.connect(lambda _checked: self.save_extension_config(log_message=False))

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
        self.status_local_time.setText(f"本地时间: {now.strftime('%H:%M:%S')}")
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
        self._set_runtime_datetime(self.sync_snapshot)
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

    def request_user1_time_sync(self, trigger_source: str) -> None:
        if self.ntp_fetch_in_progress or self.sync_in_progress:
            self.log("WARN", f"{trigger_source} 请求对时，但当前已有对时流程正在进行。")
            return
        self.log("INFO", f"{trigger_source} 请求 PC 侧 NTP 对时。")
        append_event_log(APP_DIR, "user1_sync_request", trigger_source)
        self._set_latest_event(f"{trigger_source} 请求 NTP 对时")
        self.sync_ntp_time(trigger_source=trigger_source)

    def sync_ntp_time(self, trigger_source: str = "按钮") -> None:
        if self.ntp_fetch_in_progress or self.sync_in_progress:
            self.log("WARN", f"{trigger_source} 请求对时，但当前已有对时流程正在进行。")
            return
        place = self._active_place()
        self.ntp_fetch_in_progress = True
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
            self.ntp_sync_finished.emit(snapshot_utc, error, trigger_source)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ntp_sync(self, snapshot_utc, error, trigger_source: str) -> None:
        self.ntp_fetch_in_progress = False
        if error is not None or snapshot_utc is None:
            message = str(error) if error is not None else "NTP snapshot missing"
            self.log("ERROR", f"NTP 对时失败: {message}")
            if "启动" in trigger_source or "USER1" in trigger_source:
                self.log("WARN", "板端启动后若仍停在默认时间，可视为本次 NTP 对时失败。")
            append_event_log(APP_DIR, "ntp_error", message)
            self._restore_sync_buttons_if_idle()
            self.refresh_dashboard()
            return
        place, zone_snapshot, offset_seconds = self._active_place_time_context(snapshot_utc)
        snapshot = zone_snapshot.replace(tzinfo=None)
        source_text = (
            f"{snapshot.strftime('%Y-%m-%d %H:%M:%S')} @ {place.name} "
            f"{place.timezone} {format_utc_offset(offset_seconds)}"
        )
        self._send_datetime_snapshot(snapshot, source_text)
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

    def sync_weather_and_apply(
        self,
        trigger_source: str = "按钮",
        run_tests: bool | None = None,
    ) -> None:
        self.log("INFO", "已开始更新，请稍等片刻；正在定位城市、NTP 对时并刷新天气。")
        if hasattr(self, "syncWeatherApplyButton"):
            self.syncWeatherApplyButton.setEnabled(False)
            self.syncWeatherApplyButton.setText("更新中...")
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        self._update_active_place_from_city_input(log_message=True)
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
        self._restore_sync_buttons_if_idle()
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
            self.send_command(
                build_set_weather_command(self.cached_weather_text, self.cached_weather_led_mask)
            )
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
        self.runtime_state.mode = expected_mode
        self._save_runtime_state()
        self.ui.modeCombo.setCurrentText(expected_mode)
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
        if self.config.voice_enabled and item.voice_text.strip():
            speak_text(item.voice_text.strip())
        append_event_log(APP_DIR, "schedule_fire", f"{item.title} | {item.ring_type}")
        self.log("INFO", f"提醒触发: {item.title}")
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
        self.status_connection.setText(f"连接: {port_name}")
        self.poll_timer.start()
        self.ping_timer.start()
        self.board_ready_seen = False
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
            self.status_connection.setText("连接: 本地模式")
            self.status_latency.setText("延迟: -- ms")
            self._apply_runtime_state_to_ui()
            self.refresh_dashboard()
            self.log("INFO", "已进入本地模式，数字孪生会按影子板端状态持续显示，操作只更新上位机本地配置与模拟状态。")
            return True
        if not port_name:
            self.log("WARN", "没有可连接的 COM 口。")
            return False
        if not self._open_port(port_name):
            return False
        self.query_runtime_state()
        return True

    def connect_and_apply_port(self) -> None:
        if not self.connect_port():
            return
        if self.is_connected:
            self.log("INFO", "连接完成，未自动下发设置、联网同步或运行联合测试。")

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
        self.local_mode_active = False
        self.status_connection.setText("连接: 未连接")
        self.status_latency.setText("延迟: -- ms")
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
            self.ui.modeCombo.setCurrentText(self.runtime_state.mode)
        elif query == "ALARM":
            alarm_time = QtCore.QTime.fromString(self.runtime_state.alarm_time, "HH:mm:ss")
            if alarm_time.isValid():
                self.ui.alarmTimeEdit.setTime(alarm_time)
                if hasattr(self, "scheduleAlarmTimeEdit"):
                    self.scheduleAlarmTimeEdit.setTime(alarm_time)
        elif query == "DISPLAY":
            self.ui.displayToggleCombo.setCurrentText("ON" if self.runtime_state.display_on else "OFF")
        self._local_query_notice(query)
        self.refresh_dashboard()

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
            self.runtime_state.alarm_enabled = False
            self.last_alarm = "OFF"
            self._save_runtime_state()
            action = "关闭单闹钟"
        elif upper.startswith("*SET:ALARM "):
            parts = upper.split()
            try:
                hour = int(parts[parts.index("HOUR") + 1])
                minute = int(parts[parts.index("MINUTE") + 1])
                second = int(parts[parts.index("SECOND") + 1])
                self.runtime_state.alarm_enabled = True
                self.runtime_state.alarm_time = f"{hour:02d}:{minute:02d}:{second:02d}"
                self.last_alarm = self.runtime_state.alarm_time
                self._save_runtime_state()
                alarm_time = QtCore.QTime(hour, minute, second)
                self.ui.alarmTimeEdit.setTime(alarm_time)
                if hasattr(self, "scheduleAlarmTimeEdit"):
                    self.scheduleAlarmTimeEdit.setTime(alarm_time)
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
            value = upper.removeprefix("*SET:MODE ").strip() or "DAY"
            self.runtime_state.mode = value
            self.last_mode = value
            self.ui.modeCombo.setCurrentText(value)
            self._save_runtime_state()
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
            elif key == "USER2" and self.runtime_state.weather_token:
                self._set_local_display_override(
                    self.runtime_state.weather_token.replace("_", " "),
                    5.0,
                    self.runtime_state.weather_led_mask,
                )
                action = f"虚拟按键 USER2 -> {self.runtime_state.weather_token}"
            elif key == "FORMAT":
                self.runtime_state.format = "RIGHT" if self.runtime_state.format == "LEFT" else "LEFT"
                self.ui.formatCombo.setCurrentText(self.runtime_state.format)
                action = f"虚拟按键 FORMAT -> {self.runtime_state.format}"
            elif key == "DISP":
                modes = ["TIME", "DATE", "WEEKDAY", "YEAR"]
                current = self.runtime_state.view_mode if self.runtime_state.view_mode in modes else "TIME"
                self.runtime_state.view_mode = modes[(modes.index(current) + 1) % len(modes)]
                self.local_view_scroll_key = ""
                action = f"虚拟按键 DISP -> {self.runtime_state.view_mode}"
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
        self.last_ping_monotonic = time.perf_counter()
        self.send_command("*PING")

    @property
    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command: str, expect: str | None = None) -> None:
        cleaned = command.strip()
        if not cleaned:
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
            self._start_boot_mirror_playback()
            if (time.monotonic() - self.last_ready_sync_monotonic) > 2.5:
                self.last_ready_sync_monotonic = time.monotonic()
                self.startup_sync_pending = True
                self.log("INFO", "检测到板端 RESET/启动，已显示 88888888 开机镜像；即将后台自动对时、刷新天气并同步模式。")
                QtCore.QTimer.singleShot(150, self._run_startup_sync_after_ready)
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
            self.boot_mirror_generation += 1
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
            self.runtime_state.led_mask = value & 0xFF
            self._save_runtime_state()
            self.latestLedLabel.setText(f"最新 LED: {self.latest_led_text}")
            return

        if parsed.name == "MODE":
            next_mode = parsed.data.strip() or "DAY"
            mode_origin = self._consume_mode_request(next_mode)
            self.last_mode = next_mode
            self.runtime_state.mode = next_mode
            self._save_runtime_state()
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
            self.twin.highlight_key(key)
            append_event_log(APP_DIR, "key", key)
            if key == "USER1":
                self.refresh_dashboard()
                self.request_user1_time_sync("USER1")
                return
            self.refresh_dashboard()
            self._set_latest_event(f"按键事件 -> {key}")
            return

        if parsed.name == "ALARM":
            self.last_alarm = "RINGING"
            append_event_log(APP_DIR, "alarm", "RINGING")
            if self.config.voice_enabled:
                speak_text("基础闹钟已触发")
            self.refresh_dashboard()
            self._set_latest_event("闹钟开始响铃")
            return

        if parsed.name == "ALARM_OFF":
            self.last_alarm = "OFF"
            append_event_log(APP_DIR, "alarm", "OFF")
            self.refresh_dashboard()
            self._set_latest_event("闹钟停止")
            return

        if parsed.name == "EDIT" and parsed.extra:
            self.log("INFO", f"板端保存 {parsed.data}: {parsed.extra[0]}")
            append_event_log(APP_DIR, "edit", f"{parsed.data}: {parsed.extra[0]}")
            self.refresh_dashboard()
            self._set_latest_event(f"保存 {parsed.data}: {parsed.extra[0]}")

    def _run_startup_sync_after_ready(self) -> None:
        if not self.startup_sync_pending:
            return
        self.startup_sync_pending = False
        self.sync_weather_and_apply(
            trigger_source="板端启动",
            run_tests=False,
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
            self.status_mode.setText(f"MODE: {data}")
            self.last_mode = data
            if data in {"DAY", "NIGHT"}:
                self.ui.modeCombo.setCurrentText(data)
                self.runtime_state.mode = data
                self._save_runtime_state()
                self._refresh_theme_from_mode()
                self.refresh_dashboard()
        elif query == "ALARM":
            self.last_alarm = data or "OFF"
            if data and data != "OFF":
                alarm_time = QtCore.QTime.fromString(data.replace(".", ":"), "HH:mm:ss")
                if alarm_time.isValid():
                    self.ui.alarmTimeEdit.setTime(alarm_time)
                    if hasattr(self, "scheduleAlarmTimeEdit"):
                        self.scheduleAlarmTimeEdit.setTime(alarm_time)
                        self.runtime_state.alarm_enabled = True
                        self.runtime_state.alarm_time = alarm_time.toString("HH:mm:ss")
                        self._save_runtime_state()
            else:
                self.runtime_state.alarm_enabled = False
                self._save_runtime_state()
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

    def disable_alarm(self) -> None:
        self.runtime_state.alarm_enabled = False
        self.last_alarm = "OFF"
        self._save_runtime_state()
        self._refresh_single_alarm_ui()
        self.send_command("*SET:ALARM OFF")

    def sync_host_time(self) -> None:
        self.sync_ntp_time(trigger_source="按钮")

    def _sync_host_time_step2(self) -> None:
        snapshot = self.sync_snapshot
        if snapshot is not None:
            self.send_command(build_set_time_command(snapshot))
            if self.is_connected:
                self.log("INFO", "已完成对时并写入 S800。")
            else:
                self.log("WARN", "!!! 已完成上位机本地时间更新，当前未下发板端。")
        QtCore.QTimer.singleShot(220, self._finish_sync_host_time)

    def _finish_sync_host_time(self) -> None:
        self.sync_in_progress = False
        self.sync_snapshot = None
        self._restore_sync_buttons_if_idle()
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
        if key_name.strip().upper() == "USER1":
            self.request_user1_time_sync("虚拟 USER1")
            return
        self.send_command(f"*SET:KEY {key_name}")

    def send_raw_command(self) -> None:
        self.send_command(self.ui.rawCommandEdit.text())

    def apply_schedule_alarm(self) -> None:
        time_value = self.scheduleAlarmTimeEdit.time()
        self.ui.alarmTimeEdit.setTime(time_value)
        self.apply_alarm()

    def toggle_schedule_alarm(self) -> None:
        if self.runtime_state.alarm_enabled or self.last_alarm == "RINGING":
            self.disable_alarm()
        else:
            self.apply_schedule_alarm()

    def run_automated_checks(self) -> None:
        if self.test_run_in_progress:
            return
        port_name = self.ui.portCombo.currentText().strip()
        host_only = self._is_local_mode_selected() or self._is_local_mode_active()
        if not port_name and not self.is_connected and not host_only:
            self.log("WARN", "没有可测试的 COM 口。")
            return
        self.test_run_in_progress = True
        self.test_run_started_at = time.monotonic()
        self.runChecksButton.setEnabled(False)
        self.testStatusLabel.setText("状态: 运行中")
        active_target = (
            self.serial_port.port
            if self.is_connected and self.serial_port is not None
            else ("本地模式" if host_only else port_name)
        )
        estimated = estimated_duration_seconds(host_only)
        if hasattr(self, "testEstimateLabel"):
            self.testEstimateLabel.setText(f"预计耗时: 约 {estimated} 秒 | 当前目标: {active_target}")
        self.testOutputText.setPlainText(
            f"预计耗时: 约 {estimated} 秒\n正在对 {active_target} 执行联合测试...\n"
        )
        if self.is_connected:
            self.poll_timer.stop()
            self.ping_timer.stop()
            self.read_buffer = ""

        def worker() -> None:
            try:
                progress = self.test_point_finished.emit
                if self.is_connected and self.serial_port is not None:
                    ok, output = execute_checks_on_open_port(self.serial_port, progress=progress)
                elif host_only:
                    ok, output = execute_host_only_checks(progress=progress)
                else:
                    ok, output = execute_checks_on_port(port_name, progress=progress)
            except Exception as exc:  # noqa: BLE001
                output = f"FAIL\n{exc}"
                ok = False
            self.test_run_finished.emit(output.strip(), ok)

        threading.Thread(target=worker, daemon=True).start()

    def _append_test_output_line(self, line: str) -> None:
        self.testOutputText.append(line)
        cursor = self.testOutputText.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.testOutputText.setTextCursor(cursor)

    def _finish_test_run(self, output: str, ok: bool) -> None:
        self.test_run_in_progress = False
        self.runChecksButton.setEnabled(True)
        self.last_test_ok = ok
        self.last_test_summary = "PASS" if ok else "FAIL"
        self.testStatusLabel.setText(f"状态: {'通过' if ok else '失败'}")
        if self.test_run_started_at:
            elapsed = time.monotonic() - self.test_run_started_at
            if hasattr(self, "testEstimateLabel"):
                self.testEstimateLabel.setText(f"预计耗时已结束 | 实际耗时: {elapsed:.1f} 秒")
            self.test_run_started_at = 0.0
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
