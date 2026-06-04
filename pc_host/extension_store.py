from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

CONFIG_FILENAME = "config.json"
SCHEDULES_FILENAME = "schedules.json"
LOGS_DIRNAME = "logs"
EVENT_LOG_FILENAME = "events.jsonl"
RING_TYPES = ("DEFAULT", "WORK_START", "WORK_END", "WAKE", "SONG")


@dataclass
class AppConfig:
    city_name: str = "Shanghai"
    latitude: float = 31.2304
    longitude: float = 121.4737
    timezone: str = "Asia/Shanghai"
    auto_day_night: bool = True
    theme_follow_mode: bool = True
    voice_enabled: bool = True
    quiet_night_rings: bool = True
    ntp_host: str = "pool.ntp.org"
    weather_refresh_minutes: int = 30


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

    return AppConfig(**{**asdict(AppConfig()), **payload})


def save_config(base_dir: Path, config: AppConfig) -> None:
    ensure_storage(base_dir)
    path = base_dir / CONFIG_FILENAME
    path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
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
    if (now.hour, now.minute, now.second) != (hh, mm, ss):
        return False

    if item.schedule_type == "once":
        return item.target_date == now.strftime("%Y-%m-%d")

    return now.weekday() in item.weekdays


def mark_schedule_triggered(item: ScheduleItem, now: datetime) -> None:
    item.last_triggered_slot = now.strftime("%Y-%m-%d %H:%M")


def parse_clock_hms(text: str) -> tuple[int, int, int]:
    parts = (text or "00:00:00").split(":")
    values = [0, 0, 0]
    for index in range(min(3, len(parts))):
        try:
            values[index] = int(parts[index])
        except ValueError:
            values[index] = 0
    return values[0] % 24, values[1] % 60, values[2] % 60
