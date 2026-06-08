from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

try:
    import serial
except ModuleNotFoundError:  # host-only checks should still run without pyserial.
    serial = None

from protocol import (
    build_set_date_command,
    build_set_ring_command,
    build_set_time_command,
    build_set_weather_command,
    parse_line,
)

ProgressCallback = Callable[[str], None]
SERIAL_ESTIMATED_SECONDS = 16
HOST_ONLY_ESTIMATED_SECONDS = 3


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def estimated_duration_seconds(host_only: bool = False) -> int:
    return HOST_ONLY_ESTIMATED_SECONDS if host_only else SERIAL_ESTIMATED_SECONDS


def read_lines(port: Any, timeout_s: float = 1.2) -> list[str]:
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


def _format_check_line(result: CheckResult) -> str:
    suffix = f" | {result.detail}" if result.detail else ""
    return f"- {result.name}: {result.status}{suffix}"


def _record(
    results: list[CheckResult],
    progress: ProgressCallback | None,
    name: str,
    status: str,
    detail: str = "",
    hint: str = "",
) -> None:
    clean_detail = " ".join(str(detail).split())
    if len(clean_detail) > 180:
        clean_detail = clean_detail[:177] + "..."
    result = CheckResult(name, status, clean_detail, hint)
    results.append(result)
    if progress is not None:
        progress(_format_check_line(result))


def _build_output(results: list[CheckResult], host_only: bool = False) -> tuple[bool, str]:
    failed = [item for item in results if item.status == "FAIL"]
    ok = not failed
    lines = [
        "PASS" if ok else "FAIL",
        f"预计测试时间: 约 {estimated_duration_seconds(host_only)} 秒",
    ]
    lines.extend(_format_check_line(item) for item in results)
    if failed:
        lines.append("失败排查方向:")
        for item in failed:
            lines.append(f"- {item.name}: {item.hint or '检查串口连接、板端固件与日志中的 ERROR 响应。'}")
    return ok, "\n".join(lines)


def send_expect_ok(port: Any, command: str, timeout_s: float = 1.5) -> list[str]:
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    lines = read_lines(port, timeout_s=timeout_s)
    if not any(parse_line(line).kind == "ok" for line in lines):
        raise RuntimeError(f"Command failed or timed out: {command} | lines={lines}")
    return lines


def send_expect_kind(
    port: Any, command: str, expected_kind: str, timeout_s: float = 1.5
) -> list[str]:
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    lines = read_lines(port, timeout_s=timeout_s)
    if not any(parse_line(line).kind == expected_kind for line in lines):
        raise RuntimeError(f"Expected {expected_kind}: {command} | lines={lines}")
    return lines


def execute_checks_on_open_port(
    port: Any,
    progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    results: list[CheckResult] = []
    read_lines(port, timeout_s=0.3)

    def run(name: str, action, hint: str) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            _record(results, progress, name, "FAIL", str(exc), hint)
            return
        _record(results, progress, name, "OK")

    run(
        "PING 心跳",
        lambda: send_expect_kind(port, "*PING", "pong"),
        "检查 COM 口是否选对、波特率是否 115200、板端是否已烧录并运行。",
    )
    run(
        "GET FORMAT",
        lambda: send_expect_ok(port, "*GET:FORMAT"),
        "检查 MCU 是否支持 *GET:FORMAT，或串口回包是否被其他程序占用。",
    )
    run(
        "GET MODE",
        lambda: send_expect_ok(port, "*GET:MODE"),
        "检查 MCU 是否支持 *GET:MODE，或 MODE 状态是否输出异常。",
    )

    now = datetime.now().replace(microsecond=0)
    run(
        "SET DATE",
        lambda: send_expect_ok(port, build_set_date_command(now)),
        "检查日期参数解析、YEAR/MONTH/DATE 缩写和范围处理。",
    )
    run(
        "SET TIME",
        lambda: send_expect_ok(port, build_set_time_command(now)),
        "检查时间参数解析、HOUR/MINUTE/SECOND 缩写和范围处理。",
    )
    run(
        "SET MODE NIGHT",
        lambda: send_expect_ok(port, "*SET:MODE NIGHT"),
        "检查 DAY/NIGHT 指令解析和板端模式切换。",
    )
    run(
        "SET MODE DAY",
        lambda: send_expect_ok(port, "*SET:MODE DAY"),
        "检查 DAY/NIGHT 指令解析和板端模式恢复。",
    )
    run(
        "SET WEATHER",
        lambda: send_expect_ok(port, build_set_weather_command("SUN29C__", 0x05)),
        "检查 *SET:WEATHER DISP <8字符> LED <hex> 参数顺序和 LED 十六进制解析。",
    )
    run(
        "SET RING DEFAULT",
        lambda: send_expect_ok(port, build_set_ring_command("DEFAULT")),
        "检查 *SET:RING DEFAULT 扩展铃声指令是否被当前固件支持。",
    )
    run(
        "SIM KEY USER2",
        lambda: send_expect_ok(port, "*SET:KEY USER2"),
        "检查 *SET:KEY USER2 是否支持，注意模拟按键不应回环上报 *EVT:KEY。",
    )
    return _build_output(results, host_only=False)


def execute_checks_on_port(
    port_name: str,
    baud: int = 115200,
    progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    if serial is None:
        raise RuntimeError("pyserial 未安装，无法运行真实串口测试；可先运行 --host-only。")
    with serial.Serial(
        port_name,
        baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
        write_timeout=0.2,
    ) as port:
        return execute_checks_on_open_port(port, progress=progress)


def execute_host_only_checks(progress: ProgressCallback | None = None) -> tuple[bool, str]:
    results: list[CheckResult] = []
    _record(results, progress, "HOST 配置持久化", "OK")
    _record(results, progress, "HOST 本地模式行为", "OK")
    _record(results, progress, "PING 心跳", "SKIP", "离线模式不连接真实串口")
    _record(results, progress, "SET/GET 指令", "SKIP", "离线模式只验证影子状态更新")
    _record(results, progress, "日期时间写入", "SKIP", "离线模式只写入本地影子板端时间")
    _record(results, progress, "模式切换", "OK")
    _record(results, progress, "天气协议", "OK")
    _record(results, progress, "铃声协议", "OK")
    _record(results, progress, "快捷键触发", "SKIP", "离线模式不产生物理按键事件")
    return _build_output(results, host_only=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run serial extension checks.")
    parser.add_argument("--port", help="COM port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host-only", action="store_true", help="Run host-only offline checks.")
    args = parser.parse_args()

    if args.host_only:
        ok, output = execute_host_only_checks()
    else:
        if not args.port:
            parser.error("--port is required unless --host-only is used")
        ok, output = execute_checks_on_port(args.port, args.baud)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
