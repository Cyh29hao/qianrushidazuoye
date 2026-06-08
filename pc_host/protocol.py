from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParsedLine:
    kind: str
    name: str
    data: str = ""
    extra: tuple[str, ...] = ()


def build_set_date_command(moment: datetime) -> str:
    return (
        f"*SET:DATE YEAR {moment.year:04d} "
        f"MONTH {moment.month:02d} DATE {moment.day:02d}"
    )


def build_set_time_command(moment: datetime) -> str:
    return (
        f"*SET:TIME HOUR {moment.hour:02d} "
        f"MINUTE {moment.minute:02d} SECOND {moment.second:02d}"
    )


def build_set_weather_command(display_token: str, led_mask: int) -> str:
    token = display_token[:8].ljust(8, "_")
    return f"*SET:WEATHER DISP {token} LED {led_mask:02X}"


def build_set_ring_command(ring_name: str) -> str:
    return f"*SET:RING {ring_name.strip().upper()}"


def reverse_visible_text(text: str) -> str:
    return text[::-1]


def token_to_text(token: str, dp_mask: int) -> str:
    chars: list[str] = []
    for index, raw in enumerate(token[:8].ljust(8, "_")):
        ch = " " if raw == "_" else raw
        dot_on = bool(dp_mask & (1 << index))
        if dot_on and ch == " ":
            chars.append(".")
            continue
        chars.append(ch)
        if dot_on:
            chars.append(".")
    return "".join(chars).rstrip()


def parse_line(line: str) -> ParsedLine:
    stripped = line.strip()
    if not stripped:
        return ParsedLine("empty", "")

    if stripped.startswith("*EVT:DISP "):
        parts = stripped.split(maxsplit=2)
        if len(parts) == 3:
            return ParsedLine("event", "DISP", parts[1], (parts[2],))
        if len(parts) == 2:
            try:
                int(parts[1], 16)
            except ValueError:
                return ParsedLine("error", "PARSE", stripped)
            return ParsedLine("event", "DISP", "________", (parts[1],))
        return ParsedLine("error", "PARSE", stripped)

    if stripped.startswith("*EVT:LED "):
        parts = stripped.split(maxsplit=1)
        return ParsedLine("event", "LED", parts[1] if len(parts) > 1 else "")

    if stripped.startswith("*EVT:MODE "):
        parts = stripped.split(maxsplit=1)
        return ParsedLine("event", "MODE", parts[1] if len(parts) > 1 else "")

    if stripped.startswith("*EVT:KEY "):
        parts = stripped.split(maxsplit=1)
        return ParsedLine("event", "KEY", parts[1] if len(parts) > 1 else "")

    if stripped.startswith("*EVT:EDIT "):
        parts = stripped.split(maxsplit=2)
        if len(parts) == 3:
            return ParsedLine("event", "EDIT", parts[1], (parts[2],))
        if len(parts) == 2:
            return ParsedLine("event", "EDIT", parts[1])

    if stripped == "*EVT:ALARM":
        return ParsedLine("event", "ALARM")

    if stripped == "*EVT:ALARM_OFF":
        return ParsedLine("event", "ALARM_OFF")

    if stripped.startswith("*PONG "):
        parts = stripped.split(maxsplit=1)
        return ParsedLine("pong", "PONG", parts[1] if len(parts) > 1 else "")

    if stripped == "OK":
        return ParsedLine("ok", "OK")

    if stripped.startswith("OK "):
        return ParsedLine("ok", "OK", stripped[3:])

    if stripped.startswith("ERROR "):
        return ParsedLine("error", "ERROR", stripped[6:])

    return ParsedLine("raw", "RAW", stripped)
