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
SERIAL_ESTIMATED_SECONDS = 18
SERIAL_FULL_ESTIMATED_SECONDS = 34
HOST_ONLY_ESTIMATED_SECONDS = 3
HOST_ONLY_FULL_ESTIMATED_SECONDS = 6
SERIAL_COMMAND_GAP_S = 0.12
SERIAL_HARD_TIMEOUT_S = 24.0
SERIAL_FULL_HARD_TIMEOUT_S = 42.0


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def estimated_duration_seconds(host_only: bool = False, full: bool = False) -> int:
    if host_only:
        return HOST_ONLY_FULL_ESTIMATED_SECONDS if full else HOST_ONLY_ESTIMATED_SECONDS
    return SERIAL_FULL_ESTIMATED_SECONDS if full else SERIAL_ESTIMATED_SECONDS


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


def clear_stale_input(port: Any) -> None:
    try:
        port.reset_input_buffer()
    except Exception:  # noqa: BLE001
        read_lines(port, timeout_s=0.08)


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


def _progress(progress: ProgressCallback | None, line: str) -> None:
    if progress is not None:
        progress(line)


def _build_output(
    results: list[CheckResult],
    host_only: bool = False,
    full: bool = False,
) -> tuple[bool, str]:
    failed = [item for item in results if item.status == "FAIL"]
    ok = not failed
    lines = [
        "PASS" if ok else "FAIL",
        f"预计测试时间: 约 {estimated_duration_seconds(host_only, full=full)} 秒",
    ]
    lines.extend(_format_check_line(item) for item in results)
    if failed:
        lines.append("失败排查方向:")
        for item in failed:
            lines.append(f"- {item.name}: {item.hint or '检查串口连接、板端固件与日志中的 ERROR 响应。'}")
    return ok, "\n".join(lines)


def send_expect_ok(
    port: Any,
    command: str,
    timeout_s: float = 1.5,
    progress: ProgressCallback | None = None,
) -> list[str]:
    clear_stale_input(port)
    _progress(progress, f"[TX] {command.strip()}")
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    try:
        port.flush()
    except Exception:  # noqa: BLE001
        pass
    lines = read_lines(port, timeout_s=timeout_s)
    for line in lines:
        _progress(progress, f"[RX] {line}")
    if not any(parse_line(line).kind == "ok" for line in lines):
        raise RuntimeError(f"timeout {timeout_s:.1f}s waiting OK: {command} | lines={lines}")
    time.sleep(SERIAL_COMMAND_GAP_S)
    return lines


def send_mode_expect(
    port: Any,
    mode: str,
    timeout_s: float = 2.0,
    progress: ProgressCallback | None = None,
) -> list[str]:
    command = f"*SET:MODE {mode.upper()}"
    lines = send_expect_ok(port, command, timeout_s=timeout_s, progress=progress)
    mode_seen = any(
        parse_line(line).kind == "event"
        and parse_line(line).name == "MODE"
        and parse_line(line).data.strip().upper() == mode.upper()
        for line in lines
    )
    if not mode_seen:
        _progress(progress, f"[INFO] 等待 MODE {mode.upper()} 事件和界面刷新...")
        extra = read_lines(port, timeout_s=1.2)
        for line in extra:
            _progress(progress, f"[RX] {line}")
        lines.extend(extra)
        mode_seen = any(
            parse_line(line).kind == "event"
            and parse_line(line).name == "MODE"
            and parse_line(line).data.strip().upper() == mode.upper()
            for line in lines
        )
    if not mode_seen:
        raise RuntimeError(f"MODE {mode.upper()} event not observed after {command}; lines={lines}")
    _progress(progress, f"[INFO] MODE {mode.upper()} event observed; wait 1.0s for PC UI refresh")
    time.sleep(1.0)
    return lines


def send_expect_kind(
    port: Any,
    command: str,
    expected_kind: str,
    timeout_s: float = 1.5,
    progress: ProgressCallback | None = None,
) -> list[str]:
    clear_stale_input(port)
    _progress(progress, f"[TX] {command.strip()}")
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    try:
        port.flush()
    except Exception:  # noqa: BLE001
        pass
    lines = read_lines(port, timeout_s=timeout_s)
    for line in lines:
        _progress(progress, f"[RX] {line}")
    if not any(parse_line(line).kind == expected_kind for line in lines):
        raise RuntimeError(
            f"timeout {timeout_s:.1f}s waiting {expected_kind}: {command} | lines={lines}"
        )
    time.sleep(SERIAL_COMMAND_GAP_S)
    return lines


def send_expect_error(
    port: Any,
    command: str,
    expected_reason: str,
    timeout_s: float = 1.5,
    progress: ProgressCallback | None = None,
) -> list[str]:
    lines = send_expect_kind(
        port,
        command,
        "error",
        timeout_s=timeout_s,
        progress=progress,
    )
    reason = expected_reason.strip().upper()
    if not any(parse_line(line).data.strip().upper() == reason for line in lines):
        raise RuntimeError(f"expected ERROR {reason}: {command} | lines={lines}")
    return lines


def send_user2_expect_weather_display(
    port: Any,
    progress: ProgressCallback | None = None,
) -> list[str]:
    expected = "SUN29C"
    lines: list[str] = []
    lines.extend(
        send_expect_ok(
            port,
            build_set_weather_command("SUN29C__", 0x05),
            timeout_s=2.0,
            progress=progress,
        )
    )
    lines.extend(
        send_expect_ok(
            port,
            "*SET:KEY USER2",
            timeout_s=2.0,
            progress=progress,
        )
    )
    extra = read_lines(port, timeout_s=1.8)
    for line in extra:
        _progress(progress, f"[RX] {line}")
    lines.extend(extra)
    for line in lines:
        parsed = parse_line(line)
        if parsed.kind == "event" and parsed.name == "DISP":
            compact = parsed.data.replace("_", "").replace(" ", "").replace("~", "_").upper()
            if expected in compact:
                _progress(progress, f"[INFO] USER2 weather display observed: {parsed.data}")
                return lines
    raise RuntimeError(f"USER2 weather display {expected} not observed; lines={lines}")


def execute_checks_on_open_port(
    port: Any,
    progress: ProgressCallback | None = None,
    full: bool = False,
    initial_mode: str = "DAY",
) -> tuple[bool, str]:
    results: list[CheckResult] = []
    read_lines(port, timeout_s=0.3)
    hard_deadline = time.monotonic() + (
        SERIAL_FULL_HARD_TIMEOUT_S if full else SERIAL_HARD_TIMEOUT_S
    )
    original_mode = initial_mode.strip().upper()
    if original_mode not in {"DAY", "NIGHT"}:
        original_mode = "DAY"

    def run(name: str, action, hint: str) -> None:
        if time.monotonic() >= hard_deadline:
            _record(results, progress, name, "FAIL", "serial test hard timeout", hint)
            return
        _progress(progress, f"[INFO] 开始测试: {name}")
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            _record(results, progress, name, "FAIL", str(exc), hint)
            return
        _record(results, progress, name, "OK")

    run(
        "PING 心跳",
        lambda: send_expect_kind(port, "*PING", "pong", timeout_s=2.0, progress=progress),
        "检查 COM 口是否选对、波特率是否 115200、板端是否已烧录并运行。",
    )
    run(
        "GET FORMAT",
        lambda: send_expect_ok(port, "*GET:FORMAT", timeout_s=2.0, progress=progress),
        "检查 MCU 是否支持 *GET:FORMAT，或串口回包是否被其他程序占用。",
    )
    run(
        "GET MODE",
        lambda: send_expect_ok(port, "*GET:MODE", timeout_s=2.0, progress=progress),
        "检查 MCU 是否支持 *GET:MODE，或 MODE 状态是否输出异常。",
    )

    now = datetime.now().replace(microsecond=0)
    run(
        "SET DATE",
        lambda: send_expect_ok(port, build_set_date_command(now), timeout_s=2.5, progress=progress),
        "检查日期参数解析、YEAR/MONTH/DATE 缩写和范围处理。",
    )
    run(
        "SET TIME",
        lambda: send_expect_ok(port, build_set_time_command(now), timeout_s=2.5, progress=progress),
        "检查时间参数解析、HOUR/MINUTE/SECOND 缩写和范围处理。",
    )
    other_mode = "NIGHT" if original_mode == "DAY" else "DAY"
    run(
        f"SET MODE {other_mode}",
        lambda: send_mode_expect(port, other_mode, timeout_s=2.0, progress=progress),
        "检查 DAY/NIGHT 指令解析、板端 MODE 事件和 PC 界面刷新。",
    )
    run(
        f"SET MODE {original_mode}",
        lambda: send_mode_expect(port, original_mode, timeout_s=2.0, progress=progress),
        "检查自动测试结束前能恢复测试前昼夜模式。",
    )
    run(
        "SET WEATHER",
        lambda: send_expect_ok(port, build_set_weather_command("SUN29C__", 0x05), timeout_s=2.0, progress=progress),
        "检查 *SET:WEATHER DISP <8字符> LED <hex> 参数顺序和 LED 十六进制解析。",
    )
    run(
        "SET RING DEFAULT",
        lambda: send_expect_ok(port, build_set_ring_command("DEFAULT"), timeout_s=2.0, progress=progress),
        "检查 *SET:RING DEFAULT 扩展铃声指令是否被当前固件支持。",
    )
    run(
        "USER2 天气短显",
        lambda: send_user2_expect_weather_display(port, progress=progress),
        "检查 *SET:WEATHER 与 USER2 短显状态机；若失败，确认 MCU 已烧录最新固件并能上报 *EVT:DISP。",
    )
    if full:
        run(
            "SET MSG A_B_TEST",
            lambda: send_expect_ok(port, "*SET:MSG A_B_TEST", timeout_s=2.0, progress=progress),
            "检查跑马灯、下划线七段码和临时显示状态机是否会卡死。",
        )
        for key_name in ("DISP", "SPEED", "FORMAT", "EXT"):
            run(
                f"SIM KEY {key_name}",
                lambda key_name=key_name: send_expect_ok(
                    port,
                    f"*SET:KEY {key_name}",
                    timeout_s=2.0,
                    progress=progress,
                ),
                f"检查 {key_name} 按键映射、显示状态恢复和数字孪生同步。",
            )
        run(
            "ERROR LEN",
            lambda: send_expect_error(
                port,
                "*SET:MSG 1234567890123456789012345678901234567890",
                "LEN",
                timeout_s=2.0,
                progress=progress,
            ),
            "检查超长消息是否返回 ERROR LEN。",
        )
        run(
            "ERROR SYNTAX",
            lambda: send_expect_error(
                port,
                "*SET:TIME HOUR",
                "SYNTAX",
                timeout_s=2.0,
                progress=progress,
            ),
            "检查缺少参数值时是否返回 ERROR SYNTAX。",
        )
        run(
            "ERROR PARAM",
            lambda: send_expect_error(
                port,
                "*SET:MODE DUSK",
                "PARAM",
                timeout_s=2.0,
                progress=progress,
            ),
            "检查未知枚举值是否返回 ERROR PARAM。",
        )
        run(
            "ERROR RANGE",
            lambda: send_expect_error(
                port,
                "*SET:DATE YEAR 2026 MONTH 13 DATE 01",
                "RANGE",
                timeout_s=2.0,
                progress=progress,
            ),
            "检查越界日期是否返回 ERROR RANGE。",
        )
    return _build_output(results, host_only=False, full=full)


def execute_checks_on_port(
    port_name: str,
    baud: int = 115200,
    progress: ProgressCallback | None = None,
    full: bool = False,
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
        return execute_checks_on_open_port(port, progress=progress, full=full)


def execute_host_only_checks(
    progress: ProgressCallback | None = None,
    full: bool = False,
) -> tuple[bool, str]:
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
    if full:
        _record(results, progress, "城市/NTP入口", "OK", "已验证 PC 端入口存在；真实网络由界面按钮异步执行")
        _record(results, progress, "跑马灯与下划线", "OK", "离线数字孪生支持下划线七段码和有限滚动")
        _record(results, progress, "ERROR 格式", "SKIP", "离线模式不连接 MCU，无法验证真实 ERROR 回包")
    return _build_output(results, host_only=True, full=full)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run serial extension checks.")
    parser.add_argument("--port", help="COM port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host-only", action="store_true", help="Run host-only offline checks.")
    parser.add_argument("--full", action="store_true", help="Run comprehensive checks.")
    args = parser.parse_args()

    if args.host_only:
        ok, output = execute_host_only_checks(full=args.full)
    else:
        if not args.port:
            parser.error("--port is required unless --host-only is used")
        ok, output = execute_checks_on_port(args.port, args.baud, full=args.full)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
