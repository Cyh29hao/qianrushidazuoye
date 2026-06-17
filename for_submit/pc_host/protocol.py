from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


SAFE_PROTOCOL_TOKEN_ABBREVIATIONS = {
    # Keep this table aligned with mcu/src/main.c MatchToken min lengths.
    # FORMAT and MONTH are intentionally absent: the MCU requires full tokens.
    "DISPLAY": "DISP",
    "WEATHER": "WEAT",
    "DEFAULT": "DEF",
    "WORK_START": "WORK_S",
    "WORK_END": "WORK_E",
    "MINUTE": "MIN",
    "SECOND": "SEC",
}


@dataclass
class ParsedLine:
    kind: str
    name: str
    data: str = ""
    extra: tuple[str, ...] = ()


def build_set_date_command(moment: datetime) -> str:
    return (
        "*SET:DATE YEAR MONTH DATE "
        f"{moment.year:04d} {moment.month:02d} {moment.day:02d}"
    )


def build_set_time_command(moment: datetime) -> str:
    return (
        "*SET:TIME HOUR MIN SEC "
        f"{moment.hour:02d} {moment.minute:02d} {moment.second:02d}"
    )


def build_set_alarm_command(hour: int, minute: int, second: int) -> str:
    return (
        "*SET:ALARM HOUR MIN SEC "
        f"{hour:02d} {minute:02d} {second:02d}"
    )


def build_set_weather_command(display_token: str, led_mask: int) -> str:
    cleaned = []
    for ch in display_token.strip().upper():
        if ch.isspace():
            cleaned.append("_")
        elif ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.":
            cleaned.append(ch)
        else:
            cleaned.append("_")
    token = "".join(cleaned)[:8].ljust(8, "_")
    return f"*SET:WEATHER DISP {token} LED {led_mask:02X}"


def build_set_ring_command(ring_name: str) -> str:
    return f"*SET:RING {ring_name.strip().upper()}"


def abbreviate_protocol_command(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return command
    return " ".join(
        SAFE_PROTOCOL_TOKEN_ABBREVIATIONS.get(part.upper(), part)
        for part in parts
    )


def reverse_visible_text(text: str) -> str:
    return text[::-1]


def oriented_text_to_frame(oriented: str, right_align: bool = False) -> tuple[str, int]:
    """Convert visible dotted text to FAQ v1.3 display token + dp mask.

    Dots are not display characters. They are decimal-point bits attached to
    the previous physical digit, with bit0 mapped to the leftmost digit.
    """
    cells: list[tuple[str, bool]] = []
    for raw in oriented:
        if raw == ".":
            if cells:
                ch, _ = cells[-1]
                cells[-1] = (ch, True)
            continue
        if len(cells) >= 8:
            break
        cells.append((raw, False))

    offset = max(0, 8 - len(cells)) if right_align else 0
    chars = [" "] * 8
    dp_mask = 0
    for index, (ch, has_dot) in enumerate(cells[: 8 - offset]):
        target = offset + index
        chars[target] = ch
        if has_dot:
            dp_mask |= 1 << target
    return "".join(chars), dp_mask & 0xFF


def visible_text_to_frame(visible: str, format_mode: str = "LEFT") -> tuple[str, int]:
    right_align = format_mode.strip().upper() == "RIGHT"
    oriented = reverse_visible_text(visible) if right_align else visible
    return oriented_text_to_frame(oriented, right_align=right_align)


def token_to_text(token: str, dp_mask: int) -> str:
    chars: list[str] = []
    for index, raw in enumerate(token[:8].ljust(8, "_")):
        ch = "_" if raw == "~" else (" " if raw == "_" else raw)
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
