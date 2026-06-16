from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import serial
except ModuleNotFoundError:
    serial = None

from protocol import (
    build_set_alarm_command,
    build_set_date_command,
    build_set_time_command,
)


DISPLAY_EVENT_RE = re.compile(rb"^\*EVT:DISP [!-~]{8} [0-9A-F]{2}$")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _record(checks: list[Check], name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")


def run_host_checks() -> list[Check]:
    checks: list[Check] = []
    moment = datetime(2026, 6, 15, 12, 30, 45)
    date_command = build_set_date_command(moment)
    time_command = build_set_time_command(moment)
    expected_date = "*SET:DATE YEAR MONTH DATE 2026 06 15"
    expected_time = "*SET:TIME HOUR MIN SEC 12 30 45"
    alarm_command = build_set_alarm_command(7, 30, 0)
    expected_alarm = "*SET:ALARM HOUR MIN SEC 07 30 00"
    _record(
        checks,
        "PC 日期命令使用老师分组语法",
        date_command == expected_date,
        f"actual={date_command!r}",
    )
    _record(
        checks,
        "PC 时间命令使用老师分组语法",
        time_command == expected_time,
        f"actual={time_command!r}",
    )
    _record(
        checks,
        "PC 闹钟命令使用老师分组语法",
        alarm_command == expected_alarm,
        f"actual={alarm_command!r}",
    )
    return checks


def _read_transaction(port: Any, timeout_s: float = 2.0) -> tuple[list[bytes], list[bytes]]:
    responses: list[bytes] = []
    events: list[bytes] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            time.sleep(0.005)
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith(b"*EVT:"):
            events.append(line)
            continue
        responses.append(line)
        break

    drain_deadline = time.monotonic() + 0.12
    while time.monotonic() < drain_deadline:
        raw = port.readline()
        if not raw:
            continue
        line = raw.strip()
        if line.startswith(b"*EVT:"):
            events.append(line)
        elif line:
            responses.append(line)
    return responses, events


def _send(port: Any, command: str) -> tuple[list[bytes], list[bytes]]:
    port.write((command + "\r\n").encode("ascii"))
    port.flush()
    result = _read_transaction(port)
    time.sleep(0.12)
    return result


def _expect(
    checks: list[Check],
    port: Any,
    name: str,
    command: str,
    prefix: bytes,
) -> list[bytes]:
    responses, events = _send(port, command)
    passed = len(responses) == 1 and responses[0].upper().startswith(prefix)
    rendered = [line.decode("ascii", errors="backslashreplace") for line in responses]
    _record(checks, name, passed, f"{command} -> {rendered or ['TIMEOUT']}")
    return events


def run_serial_checks(port_name: str, baud: int) -> list[Check]:
    if serial is None:
        raise RuntimeError("pyserial 未安装")

    checks: list[Check] = []
    all_events: list[bytes] = []
    with serial.Serial(port_name, baud, timeout=0.05, write_timeout=0.5) as port:
        time.sleep(0.15)
        port.reset_input_buffer()
        port.reset_output_buffer()

        all_events += _expect(checks, port, "RST 首包有应答", "*RST", b"OK")
        all_events += _expect(
            checks,
            port,
            "老师 DATE 三参数分组",
            "*SET:DATE YEAR MONTH DATE 2026 06 15",
            b"OK",
        )
        all_events += _expect(
            checks,
            port,
            "老师 TIME 三参数分组",
            "*SET:TIME HOUR MIN SEC 12 30 45",
            b"OK",
        )
        all_events += _expect(
            checks,
            port,
            "老师 ALARM 三参数分组",
            "*SET:ALARM HOUR MIN SEC 12 31 00",
            b"OK",
        )

        for suffix in ("MIN", "MINU", "MINUT"):
            all_events += _expect(
                checks,
                port,
                f"合法缩写 {suffix}",
                f"*SET:TIME HOUR {suffix} SEC 08 30 00",
                b"OK",
            )

        all_events += _expect(
            checks,
            port,
            "非法缩写 MONT",
            "*SET:DATE YEAR MONT DATE 2026 01 01",
            b"ERROR",
        )
        all_events += _expect(
            checks,
            port,
            "非法缩写 MI",
            "*SET:TIME HOUR MI SEC 09 00 00",
            b"ERROR",
        )
        all_events += _expect(
            checks,
            port,
            "非法命令缩写 FOR",
            "*SET:FOR RIGHT",
            b"ERROR",
        )

        all_events += _expect(
            checks,
            port,
            "兼容旧 DATE 交替语法",
            "*SET:DATE YEAR 2026 MONTH 06 DATE 16",
            b"OK",
        )
        all_events += _expect(
            checks,
            port,
            "兼容旧 TIME 交替语法",
            "*SET:TIME HOUR 13 MINUTE 32 SECOND 46",
            b"OK",
        )
        all_events += _expect(
            checks,
            port,
            "兼容旧 ALARM 交替语法",
            "*SET:ALARM HOUR 13 MINUTE 33 SECOND 00",
            b"OK",
        )

        ping_passed = True
        ping_detail: list[str] = []
        for index in range(10):
            responses, events = _send(port, "*PING")
            all_events += events
            ok = len(responses) == 1 and responses[0].upper().startswith(b"*PONG ")
            ping_passed &= ok
            ping_detail.append(f"{index + 1}:{'OK' if ok else 'TIMEOUT/ERROR'}")
        _record(checks, "连续 10 次 PING 无卡死", ping_passed, ", ".join(ping_detail))

    display_events = [event for event in all_events if event.startswith(b"*EVT:DISP")]
    malformed = [event for event in display_events if DISPLAY_EVENT_RE.fullmatch(event) is None]
    _record(
        checks,
        "DISP 事件严格 ASCII 且格式完整",
        bool(display_events) and not malformed,
        f"count={len(display_events)}, malformed={malformed!r}",
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Teacher protocol regression checks.")
    parser.add_argument("--port", help="真实串口，例如 COM5")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    checks = run_host_checks()
    if args.port:
        checks.extend(run_serial_checks(args.port, args.baud))
    failed = [check for check in checks if not check.passed]
    print(f"\nSUMMARY: PASS={len(checks) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
