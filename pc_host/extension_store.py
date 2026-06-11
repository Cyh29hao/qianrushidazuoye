from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.json"
SCHEDULES_FILENAME = "schedules.json"
RUNTIME_STATE_FILENAME = "runtime_state.json"
LOGS_DIRNAME = "logs"
EVENT_LOG_FILENAME = "events.jsonl"
RING_TYPES = ("DEFAULT", "WORK_START", "WORK_END", "WAKE", "SONG")


@dataclass
class SavedPlace:
    name: str = "上海"
    latitude: float = 31.2304
    longitude: float = 121.4737
    timezone: str = "Asia/Shanghai"
    utc_offset_seconds: int = 8 * 3600
    country: str = "中国"


def _default_saved_places() -> list[SavedPlace]:
    return [SavedPlace() for _ in range(5)]


@dataclass
class AppConfig:
    city_name: str = "上海"
    latitude: float = 31.2304
    longitude: float = 121.4737
    timezone: str = "Asia/Shanghai"
    user_name: str = "用户"
    auto_day_night: bool = True
    theme_follow_mode: bool = True
    voice_enabled: bool = True
    quiet_night_rings: bool = True
    ntp_host: str = "pool.ntp.org"
    weather_refresh_minutes: int = 30
    saved_places: list[SavedPlace] = field(default_factory=_default_saved_places)
    active_place_index: int = 0
    auto_run_tests_on_start: bool = False
    app_version: str = "2.2"


@dataclass
class RuntimeState:
    display_on: bool = True
    format: str = "LEFT"
    mode: str = "DAY"
    view_mode: str = "TIME"
    alarm_enabled: bool = False
    alarm_time: str = "07:30:00"
    led_mask: int = 0x00
    message_text: str = ""
    weather_token: str = ""
    weather_led_mask: int = 0x00
    board_datetime_iso: str = ""
    shadow_saved_at_utc_iso: str = ""


@dataclass
class ScheduleItem:
    item_id: str
    title: str
    board_label: str
    trigger_time: str
    schedule_type: str = "weekly"
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    target_date: str = ""
    ring_type: str = "DEFAULT"
    enabled: bool = True
    voice_text: str = ""
    last_triggered_slot: str = ""

    @staticmethod
    def create_default() -> "ScheduleItem":
        return ScheduleItem(
            item_id=uuid.uuid4().hex[:12],
            title="课程提醒",
            board_label="CLASS___",
            trigger_time="08:00:00",
        )


def ensure_storage(base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / LOGS_DIRNAME).mkdir(parents=True, exist_ok=True)


def load_config(base_dir: Path) -> AppConfig:
    ensure_storage(base_dir)
    path = base_dir / CONFIG_FILENAME
    if not path.exists():
        config = AppConfig()
        save_config(base_dir, config)
        return config

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = AppConfig()
        save_config(base_dir, config)
        return config

    return _normalize_config(payload)


def save_config(base_dir: Path, config: AppConfig) -> None:
    ensure_storage(base_dir)
    path = base_dir / CONFIG_FILENAME
    _sync_legacy_city_fields(config)
    path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_runtime_state(base_dir: Path) -> RuntimeState:
    ensure_storage(base_dir)
    path = base_dir / RUNTIME_STATE_FILENAME
    if not path.exists():
        state = RuntimeState()
        save_runtime_state(base_dir, state)
        return state

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = RuntimeState()
        save_runtime_state(base_dir, state)
        return state

    if not isinstance(payload, dict):
        state = RuntimeState()
        save_runtime_state(base_dir, state)
        return state

    defaults = RuntimeState()
    view_mode = str(payload.get("view_mode", defaults.view_mode) or defaults.view_mode).upper()
    if view_mode not in {"TIME", "DATE", "WEEKDAY", "YEAR"}:
        view_mode = defaults.view_mode
    display_format = str(payload.get("format", defaults.format) or defaults.format).upper()
    if display_format not in {"LEFT", "RIGHT"}:
        display_format = defaults.format
    mode = str(payload.get("mode", defaults.mode) or defaults.mode).upper()
    if mode not in {"DAY", "NIGHT"}:
        mode = defaults.mode
    return RuntimeState(
        display_on=bool(payload.get("display_on", defaults.display_on)),
        format=display_format,
        mode=mode,
        view_mode=view_mode,
        alarm_enabled=bool(payload.get("alarm_enabled", defaults.alarm_enabled)),
        alarm_time=str(payload.get("alarm_time", defaults.alarm_time) or defaults.alarm_time),
        led_mask=int(payload.get("led_mask", defaults.led_mask)) & 0xFF,
        message_text=str(payload.get("message_text", defaults.message_text) or defaults.message_text),
        weather_token=str(payload.get("weather_token", defaults.weather_token) or defaults.weather_token),
        weather_led_mask=int(payload.get("weather_led_mask", defaults.weather_led_mask)) & 0xFF,
        board_datetime_iso=str(
            payload.get("board_datetime_iso", defaults.board_datetime_iso)
            or defaults.board_datetime_iso
        ),
        shadow_saved_at_utc_iso=str(
            payload.get("shadow_saved_at_utc_iso", defaults.shadow_saved_at_utc_iso)
            or defaults.shadow_saved_at_utc_iso
        ),
    )


def save_runtime_state(base_dir: Path, state: RuntimeState) -> None:
    ensure_storage(base_dir)
    path = base_dir / RUNTIME_STATE_FILENAME
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_schedules(base_dir: Path) -> list[ScheduleItem]:
    ensure_storage(base_dir)
    path = base_dir / SCHEDULES_FILENAME
    if not path.exists():
        save_schedules(base_dir, [])
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        save_schedules(base_dir, [])
        return []

    result: list[ScheduleItem] = []
    if not isinstance(payload, list):
        return result

    for item in payload:
        if not isinstance(item, dict):
            continue
        merged = asdict(ScheduleItem.create_default())
        merged.update(item)
        weekdays = merged.get("weekdays", [])
        if isinstance(weekdays, list):
            normalized_weekdays: list[int] = []
            weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            for value in weekdays:
                if isinstance(value, int):
                    if 0 <= value < 7:
                        normalized_weekdays.append(value)
                elif isinstance(value, str) and value in weekday_names:
                    normalized_weekdays.append(weekday_names.index(value))
            merged["weekdays"] = normalized_weekdays
        if merged.get("schedule_type") == "date":
            merged["schedule_type"] = "once"
        result.append(ScheduleItem(**merged))
    return result


def save_schedules(base_dir: Path, schedules: list[ScheduleItem]) -> None:
    ensure_storage(base_dir)
    path = base_dir / SCHEDULES_FILENAME
    payload = [asdict(item) for item in schedules]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_event_log(base_dir: Path, event_type: str, payload: Any) -> None:
    ensure_storage(base_dir)
    line = {
        "when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": event_type,
        "detail": payload,
    }
    path = base_dir / LOGS_DIRNAME / EVENT_LOG_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_recent_event_logs(base_dir: Path, limit: int = 400) -> list[dict[str, Any]]:
    path = base_dir / LOGS_DIRNAME / EVENT_LOG_FILENAME
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    events: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "kind" in decoded and "detail" in decoded:
            events.append(decoded)
            continue
        events.append(
            {
                "when": decoded.get("timestamp", "--"),
                "kind": decoded.get("event_type", "event"),
                "detail": decoded.get("payload", ""),
            }
        )
    return events


def normalize_board_token(text: str) -> str:
    cleaned = "".join(
        ch.upper() if ch.isascii() and ch.isalnum() else "_"
        for ch in text.strip()
    )
    cleaned = cleaned[:8].ljust(8, "_")
    return cleaned or "NOTICE__"


def normalize_board_message(text: str, max_len: int = 32) -> str:
    cleaned = "".join(
        ch.upper() if ch.isascii() and (ch.isalnum() or ch in {"-", "_", "."}) else "_"
        for ch in text.strip()
    )
    cleaned = cleaned[:max_len].strip("_")
    return cleaned or normalize_board_token(text).strip("_") or "NOTICE"


def weekday_text(weekdays: list[int]) -> str:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    chosen = [names[index] for index in weekdays if 0 <= index < len(names)]
    return ",".join(chosen) if chosen else "-"


def schedule_trigger_matches(item: ScheduleItem, now: datetime) -> bool:
    if not item.enabled:
        return False

    slot = now.strftime("%Y-%m-%d %H:%M")
    if item.last_triggered_slot == slot:
        return False

    hh, mm, ss = parse_clock_hms(item.trigger_time)
    if (now.hour, now.minute) != (hh, mm):
        return False
    if now.second < ss:
        return False

    if item.schedule_type == "once":
        return item.target_date == now.strftime("%Y-%m-%d")

    return now.weekday() in item.weekdays


def mark_schedule_triggered(item: ScheduleItem, now: datetime) -> None:
    item.last_triggered_slot = now.strftime("%Y-%m-%d %H:%M")
    if item.schedule_type == "once":
        item.enabled = False


def parse_clock_hms(text: str) -> tuple[int, int, int]:
    parts = (text or "00:00:00").split(":")
    values = [0, 0, 0]
    for index in range(min(3, len(parts))):
        try:
            values[index] = int(parts[index])
        except ValueError:
            values[index] = 0
    return values[0] % 24, values[1] % 60, values[2] % 60


def _normalize_config(payload: dict[str, Any]) -> AppConfig:
    defaults = AppConfig()
    config = AppConfig(
        city_name=str(payload.get("city_name", defaults.city_name) or defaults.city_name),
        latitude=float(payload.get("latitude", defaults.latitude)),
        longitude=float(payload.get("longitude", defaults.longitude)),
        timezone=str(payload.get("timezone", defaults.timezone) or defaults.timezone),
        user_name=str(payload.get("user_name", defaults.user_name) or defaults.user_name),
        auto_day_night=bool(payload.get("auto_day_night", defaults.auto_day_night)),
        theme_follow_mode=bool(payload.get("theme_follow_mode", defaults.theme_follow_mode)),
        voice_enabled=bool(payload.get("voice_enabled", defaults.voice_enabled)),
        quiet_night_rings=bool(payload.get("quiet_night_rings", defaults.quiet_night_rings)),
        ntp_host=str(payload.get("ntp_host", defaults.ntp_host) or defaults.ntp_host),
        weather_refresh_minutes=int(
            payload.get("weather_refresh_minutes", defaults.weather_refresh_minutes)
        ),
        active_place_index=int(
            payload.get("active_place_index", defaults.active_place_index)
        ),
        auto_run_tests_on_start=False,
        app_version=str(payload.get("app_version", defaults.app_version) or defaults.app_version),
    )
    raw_places = payload.get("saved_places")
    if isinstance(raw_places, list) and raw_places:
        config.saved_places = [_normalize_place_entry(item) for item in raw_places[:5]]
    else:
        config.saved_places = _default_saved_places()
        config.saved_places[0] = SavedPlace(
            name=config.city_name,
            latitude=config.latitude,
            longitude=config.longitude,
            timezone=config.timezone,
            utc_offset_seconds=8 * 3600,
            country=str(payload.get("country", "中国") or "中国"),
        )
    while len(config.saved_places) < 5:
        config.saved_places.append(SavedPlace())
    if not (0 <= config.active_place_index < len(config.saved_places)):
        config.active_place_index = 0
    _sync_legacy_city_fields(config)
    return config


def _normalize_place_entry(raw: Any) -> SavedPlace:
    if not isinstance(raw, dict):
        return SavedPlace()
    return SavedPlace(
        name=str(raw.get("name", "上海") or "上海"),
        latitude=float(raw.get("latitude", 31.2304)),
        longitude=float(raw.get("longitude", 121.4737)),
        timezone=str(raw.get("timezone", "Asia/Shanghai") or "Asia/Shanghai"),
        utc_offset_seconds=int(raw.get("utc_offset_seconds", 8 * 3600)),
        country=str(raw.get("country", "中国") or "中国"),
    )


def _sync_legacy_city_fields(config: AppConfig) -> None:
    if not config.saved_places:
        config.saved_places = _default_saved_places()
    if not (0 <= config.active_place_index < len(config.saved_places)):
        config.active_place_index = 0
    current = config.saved_places[config.active_place_index]
    config.city_name = current.name
    config.latitude = current.latitude
    config.longitude = current.longitude
    config.timezone = current.timezone
