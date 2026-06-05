from __future__ import annotations

import argparse
import time
from datetime import datetime

import serial

from protocol import (
    build_set_date_command,
    build_set_ring_command,
    build_set_time_command,
    build_set_weather_command,
    parse_line,
)


def read_lines(port: serial.Serial, timeout_s: float = 1.2) -> list[str]:
    deadline = time.time() + timeout_s
    buffer = ""
    lines: list[str] = []
    while time.time() < deadline:
        chunk = port.read(port.in_waiting or 1)
        if chunk:
            buffer += chunk.decode("ascii", errors="ignore")
            normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
            parts = normalized.split("\n")
            buffer = parts.pop() if not normalized.endswith("\n") else ""
            for line in parts:
                if line:
                    lines.append(line.strip())
        else:
            time.sleep(0.02)
    return lines


def send_expect_ok(port: serial.Serial, command: str, timeout_s: float = 1.5) -> list[str]:
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    lines = read_lines(port, timeout_s=timeout_s)
    if not any(parse_line(line).kind == "ok" for line in lines):
        raise RuntimeError(f"Command failed or timed out: {command} | lines={lines}")
    return lines


def send_expect_kind(
    port: serial.Serial, command: str, expected_kind: str, timeout_s: float = 1.5
) -> list[str]:
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    lines = read_lines(port, timeout_s=timeout_s)
    if not any(parse_line(line).kind == expected_kind for line in lines):
        raise RuntimeError(f"Expected {expected_kind}: {command} | lines={lines}")
    return lines


def execute_checks_on_open_port(port: serial.Serial) -> tuple[bool, str]:
    checks: list[tuple[str, str]] = []
    try:
        read_lines(port, timeout_s=0.3)
        checks.append(("PING", "ok"))
        send_expect_kind(port, "*PING", "pong")

        checks.append(("GET FORMAT", "ok"))
        send_expect_ok(port, "*GET:FORMAT")

        checks.append(("GET MODE", "ok"))
        send_expect_ok(port, "*GET:MODE")

        now = datetime.now().replace(microsecond=0)
        checks.append(("SET DATE", "ok"))
        send_expect_ok(port, build_set_date_command(now))

        checks.append(("SET TIME", "ok"))
        send_expect_ok(port, build_set_time_command(now))

        checks.append(("SET MODE NIGHT", "ok"))
        send_expect_ok(port, "*SET:MODE NIGHT")

        checks.append(("SET MODE DAY", "ok"))
        send_expect_ok(port, "*SET:MODE DAY")

        checks.append(("SET WEATHER", "ok"))
        send_expect_ok(port, build_set_weather_command("SUN29C__", 0x05))

        checks.append(("SET RING DEFAULT", "ok"))
        send_expect_ok(port, build_set_ring_command("DEFAULT"))

        checks.append(("SIM KEY USER2", "ok"))
        send_expect_ok(port, "*SET:KEY USER2")
    except Exception as exc:  # noqa: BLE001
        lines = ["FAIL", str(exc)]
        lines.extend(f"- {name}: {status}" for name, status in checks)
        return False, "\n".join(lines)

    lines = ["PASS"]
    lines.extend(f"- {name}: {status}" for name, status in checks)
    return True, "\n".join(lines)


def execute_checks_on_port(port_name: str, baud: int = 115200) -> tuple[bool, str]:
    with serial.Serial(
        port_name,
        baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
        write_timeout=0.2,
    ) as port:
        return execute_checks_on_open_port(port)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run serial extension checks.")
    parser.add_argument("--port", required=True, help="COM port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    ok, output = execute_checks_on_port(args.port, args.baud)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
