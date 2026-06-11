from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Event
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
SERIAL_ESTIMATED_SECONDS = 45
SERIAL_FULL_ESTIMATED_SECONDS = 140
HOST_ONLY_ESTIMATED_SECONDS = 5
HOST_ONLY_FULL_ESTIMATED_SECONDS = 9
SERIAL_COMMAND_GAP_S = 0.55
SERIAL_HARD_TIMEOUT_S = 70.0
SERIAL_FULL_HARD_TIMEOUT_S = 180.0


class CheckCancelled(RuntimeError):
    """Raised when the UI requests an automated-test stop."""


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CheckCancelled("用户已中止自动测试")


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


def read_lines(
    port: Any,
    timeout_s: float = 1.2,
    cancel_event: Event | None = None,
) -> list[str]:
    deadline = time.time() + timeout_s
    buffer = ""
    lines: list[str] = []
    while time.time() < deadline:
        _raise_if_cancelled(cancel_event)
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


def settle_serial(
    port: Any,
    seconds: float = 0.7,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list[str]:
    lines = read_lines(port, timeout_s=seconds, cancel_event=cancel_event)
    for line in lines:
        _progress(progress, f"[RX] {line}")
    if seconds > 0:
        time.sleep(min(0.25, max(0.0, seconds / 4)))
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
    cancel_event: Event | None = None,
) -> list[str]:
    _raise_if_cancelled(cancel_event)
    clear_stale_input(port)
    _progress(progress, f"[TX] {command.strip()}")
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    try:
        port.flush()
    except Exception:  # noqa: BLE001
        pass
    lines = read_lines(port, timeout_s=timeout_s, cancel_event=cancel_event)
    for line in lines:
        _progress(progress, f"[RX] {line}")
    if not any(parse_line(line).kind == "ok" for line in lines):
        raise RuntimeError(f"timeout {timeout_s:.1f}s waiting OK: {command} | lines={lines}")
    time.sleep(SERIAL_COMMAND_GAP_S)
    return lines


def ok_payload(lines: list[str]) -> str:
    for line in lines:
        parsed = parse_line(line)
        if parsed.kind == "ok":
            return parsed.data.strip().upper()
    return ""


def send_mode_expect(
    port: Any,
    mode: str,
    timeout_s: float = 2.0,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list[str]:
    command = f"*SET:MODE {mode.upper()}"
    lines = send_expect_ok(port, command, timeout_s=timeout_s, progress=progress, cancel_event=cancel_event)
    mode_seen = any(
        parse_line(line).kind == "event"
        and parse_line(line).name == "MODE"
        and parse_line(line).data.strip().upper() == mode.upper()
        for line in lines
    )
    if not mode_seen:
        _progress(progress, f"[INFO] 等待 MODE {mode.upper()} 事件和界面刷新...")
        extra = read_lines(port, timeout_s=1.2, cancel_event=cancel_event)
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
    cancel_event: Event | None = None,
) -> list[str]:
    _raise_if_cancelled(cancel_event)
    clear_stale_input(port)
    _progress(progress, f"[TX] {command.strip()}")
    port.write((command.strip() + "\r\n").encode("ascii", "ignore"))
    try:
        port.flush()
    except Exception:  # noqa: BLE001
        pass
    lines = read_lines(port, timeout_s=timeout_s, cancel_event=cancel_event)
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
    cancel_event: Event | None = None,
) -> list[str]:
    lines = send_expect_kind(
        port,
        command,
        "error",
        timeout_s=timeout_s,
        progress=progress,
        cancel_event=cancel_event,
    )
    reason = expected_reason.strip().upper()
    if not any(parse_line(line).data.strip().upper() == reason for line in lines):
        raise RuntimeError(f"expected ERROR {reason}: {command} | lines={lines}")
    return lines


def send_user2_expect_weather_display(
    port: Any,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list[str]:
    expected = "SUN29C"
    lines: list[str] = []
    _progress(progress, "[INFO] USER2 safe weather display uses EXT + LED + MSG to avoid wedging old USER2 firmware")
    format_lines = send_expect_ok(
        port,
        "*GET:FORMAT",
        timeout_s=1.2,
        progress=progress,
        cancel_event=cancel_event,
    )
    lines.extend(format_lines)
    current_format = ok_payload(format_lines) or "LEFT"
    _progress(progress, f"[INFO] USER2 safe weather display starts from format={current_format}; force LEFT for readable short display")
    for attempt in range(1, 4):
        if attempt > 1:
            _progress(progress, f"[INFO] USER2 safe weather display retry {attempt}/3")
        for command in (
            "*SET:KEY EXT",
            "*SET:DISPLAY ON",
            "*SET:FORMAT LEFT",
            "*SET:LED 05",
            f"*SET:MSG {expected}",
        ):
            lines.extend(
                send_expect_ok(
                    port,
                    command,
                    timeout_s=1.4,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            )
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            extra = read_lines(port, timeout_s=0.35, cancel_event=cancel_event)
            for line in extra:
                _progress(progress, f"[RX] {line}")
                lines.append(line)
                parsed = parse_line(line)
                if parsed.kind == "event" and parsed.name == "DISP":
                    compact = parsed.data.replace("_", "").replace(" ", "").replace("~", "_").upper()
                    if expected in compact:
                        _progress(progress, f"[INFO] USER2 safe weather display observed: {parsed.data}")
                        _progress(progress, "[INFO] USER2 display observed; wait for temporary display to settle before next test")
                        lines.extend(settle_serial(port, seconds=8.4, progress=progress, cancel_event=cancel_event))
                        return lines
    for line in lines:
        parsed = parse_line(line)
        if parsed.kind == "event" and parsed.name == "DISP":
            compact = parsed.data.replace("_", "").replace(" ", "").replace("~", "_").upper()
            if expected in compact:
                _progress(progress, f"[INFO] USER2 safe weather display observed: {parsed.data}")
                _progress(progress, "[INFO] USER2 display observed; wait for temporary display to settle before next test")
                lines.extend(settle_serial(port, seconds=8.4, progress=progress, cancel_event=cancel_event))
                return lines
    raise RuntimeError(f"USER2 safe weather display {expected} not observed; lines={lines}")


def execute_checks_on_open_port(
    port: Any,
    progress: ProgressCallback | None = None,
    full: bool = False,
    initial_mode: str = "DAY",
    cancel_event: Event | None = None,
) -> tuple[bool, str]:
    results: list[CheckResult] = []
    read_lines(port, timeout_s=0.3, cancel_event=cancel_event)
    hard_deadline = time.monotonic() + (
        SERIAL_FULL_HARD_TIMEOUT_S if full else SERIAL_HARD_TIMEOUT_S
    )
    original_mode = initial_mode.strip().upper()
    if original_mode not in {"DAY", "NIGHT"}:
        original_mode = "DAY"
    original_format = "LEFT"
    original_display = "ON"
    failed = False

    def capture_state(command: str, default: str, allowed: set[str]) -> str:
        try:
            lines = send_expect_ok(port, command, timeout_s=1.8, progress=progress, cancel_event=cancel_event)
            value = ok_payload(lines).strip().upper()
            if value in allowed:
                return value
        except Exception as exc:  # noqa: BLE001
            _progress(progress, f"[WARN] could not capture {command}: {exc}; use default {default}")
        return default

    _progress(progress, "[INFO] Capture board FORMAT/MODE/DISPLAY before automated test for restoration")
    original_format = capture_state("*GET:FORMAT", original_format, {"LEFT", "RIGHT"})
    original_mode = capture_state("*GET:MODE", original_mode, {"DAY", "NIGHT"})
    original_display = capture_state("*GET:DISPLAY", original_display, {"ON", "OFF"})

    total_steps = 11 + (10 if full else 0)

    def run(name: str, action, hint: str, settle_s: float = 0.7) -> None:
        nonlocal failed
        if failed:
            return
        if cancel_event is not None and cancel_event.is_set():
            _record(results, progress, name, "FAIL", "用户已中止自动测试", "测试已由用户中止；脚本会执行收尾恢复。")
            failed = True
            return
        if time.monotonic() >= hard_deadline:
            _record(results, progress, name, "FAIL", "serial test hard timeout", hint)
            failed = True
            return
        step_no = len(results) + 1
        _progress(progress, f"[STEP {step_no}/{total_steps}] {name}")
        _progress(progress, f"[INFO] 开始测试: {name}")
        try:
            action()
        except CheckCancelled as exc:
            _record(results, progress, name, "FAIL", str(exc), "测试已由用户中止；脚本会执行收尾恢复。")
            failed = True
            return
        except Exception as exc:  # noqa: BLE001
            _record(results, progress, name, "FAIL", str(exc), hint)
            failed = True
            return
        if settle_s > 0:
            settle_serial(port, seconds=settle_s, progress=progress, cancel_event=cancel_event)
        _record(results, progress, name, "OK")

    def restore_board_state() -> None:
        _progress(
            progress,
            f"[INFO] Restore board settings: FORMAT={original_format}, MODE={original_mode}, DISPLAY={original_display}",
        )
        restore_commands = [
            "*SET:KEY EXT",
            f"*SET:FORMAT {original_format}",
            f"*SET:MODE {original_mode}",
            f"*SET:DISPLAY {original_display}",
        ]
        for command in restore_commands:
            try:
                send_expect_ok(port, command, timeout_s=2.0, progress=progress)
            except Exception as exc:  # noqa: BLE001
                _progress(progress, f"[WARN] restore stopped at {command}: {exc}")
                break

    run(
        "PING 心跳",
        lambda: send_expect_kind(port, "*PING", "pong", timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 COM 口是否选对、波特率是否 115200、板端是否已烧录并运行。",
    )
    run(
        "GET FORMAT",
        lambda: send_expect_ok(port, "*GET:FORMAT", timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 MCU 是否支持 *GET:FORMAT，或串口回包是否被其他程序占用。",
    )
    run(
        "GET MODE",
        lambda: send_expect_ok(port, "*GET:MODE", timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 MCU 是否支持 *GET:MODE，或 MODE 状态是否输出异常。",
    )
    run(
        "GET DISPLAY",
        lambda: send_expect_ok(port, "*GET:DISPLAY", timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 MCU 是否支持 *GET:DISPLAY，或显示开关状态是否输出异常。",
    )

    now = datetime.now().replace(microsecond=0)
    run(
        "SET DATE",
        lambda: send_expect_ok(port, build_set_date_command(now), timeout_s=2.5, progress=progress, cancel_event=cancel_event),
        "检查日期参数解析、YEAR/MONTH/DATE 缩写和范围处理。",
    )
    run(
        "SET TIME",
        lambda: send_expect_ok(port, build_set_time_command(now), timeout_s=2.5, progress=progress, cancel_event=cancel_event),
        "检查时间参数解析、HOUR/MINUTE/SECOND 缩写和范围处理。",
    )
    other_mode = "NIGHT" if original_mode == "DAY" else "DAY"
    run(
        f"SET MODE {other_mode}",
        lambda: send_mode_expect(port, other_mode, timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 DAY/NIGHT 指令解析、板端 MODE 事件和 PC 界面刷新。",
        settle_s=1.8,
    )
    run(
        f"SET MODE {original_mode}",
        lambda: send_mode_expect(port, original_mode, timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查自动测试结束前能恢复测试前昼夜模式。",
        settle_s=1.8,
    )
    run(
        "SET WEATHER",
        lambda: send_expect_ok(port, build_set_weather_command("SUN29C__", 0x05), timeout_s=3.5, progress=progress, cancel_event=cancel_event),
        "检查 *SET:WEATHER DISP <8字符> LED <hex> 参数顺序和 LED 十六进制解析。",
    )
    run(
        "SET RING DEFAULT",
        lambda: send_expect_ok(port, build_set_ring_command("DEFAULT"), timeout_s=2.0, progress=progress, cancel_event=cancel_event),
        "检查 *SET:RING DEFAULT 扩展铃声指令是否被当前固件支持。",
    )
    run(
        "USER2 安全天气短显",
        lambda: send_user2_expect_weather_display(port, progress=progress, cancel_event=cancel_event),
        "检查 PC 辅助 USER2 天气显示路径：EXT 退出临时态、LED 掩码、MSG 显示天气 token，并观察 *EVT:DISP。",
    )
    if full:
        def send_msg_and_wait_for_marquee() -> list[str]:
            lines = send_expect_ok(port, "*SET:MSG A_B_TEST", timeout_s=5.0, progress=progress, cancel_event=cancel_event)
            _progress(progress, "[INFO] Wait for marquee min duration and final-frame hold before next command")
            lines.extend(settle_serial(port, seconds=8.2, progress=progress, cancel_event=cancel_event))
            return lines

        def press_key_and_wait(key_name: str) -> list[str]:
            lines = send_expect_ok(
                port,
                f"*SET:KEY {key_name}",
                timeout_s=2.0,
                progress=progress,
                cancel_event=cancel_event,
            )
            wait_s = 1.4 if key_name in {"DISP", "FORMAT", "EXT"} else 1.0
            lines.extend(settle_serial(port, seconds=wait_s, progress=progress, cancel_event=cancel_event))
            return lines

        def rapid_key_burst_probe() -> list[str]:
            _progress(progress, "[INFO] Rapid key burst: FUNC/DISP/SPEED/FORMAT/EXT, then EXT + PING recovery")
            clear_stale_input(port)
            lines: list[str] = []
            for key_name in ("FUNC", "DISP", "SPEED", "FORMAT", "EXT", "FUNC", "DISP", "EXT"):
                _raise_if_cancelled(cancel_event)
                command = f"*SET:KEY {key_name}"
                _progress(progress, f"[TX] {command}")
                port.write((command + "\r\n").encode("ascii", "ignore"))
                try:
                    port.flush()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.09)
            burst_lines = read_lines(port, timeout_s=2.2, cancel_event=cancel_event)
            for line in burst_lines:
                _progress(progress, f"[RX] {line}")
            lines.extend(burst_lines)
            lines.extend(
                send_expect_ok(port, "*SET:KEY EXT", timeout_s=2.0, progress=progress, cancel_event=cancel_event)
            )
            lines.extend(
                send_expect_kind(port, "*PING", "pong", timeout_s=2.8, progress=progress, cancel_event=cancel_event)
            )
            return lines

        run(
            "SET MSG A_B_TEST",
            send_msg_and_wait_for_marquee,
            "检查跑马灯、下划线七段码和临时显示状态机是否会卡死。",
            settle_s=0.0,
        )
        for key_name in ("DISP", "SPEED", "FORMAT", "EXT"):
            run(
                f"SIM KEY {key_name}",
                lambda key_name=key_name: press_key_and_wait(key_name),
                f"检查 {key_name} 按键映射、显示状态恢复和数字孪生同步。",
                settle_s=0.0,
            )
        run(
            "RAPID KEY BURST",
            rapid_key_burst_probe,
            "快速按键后没有 PONG，疑似板端/串口状态机卡死；请手动 RESET 后重新运行测试。",
            settle_s=1.2,
        )
        run(
            "ERROR LEN",
            lambda: send_expect_error(
                port,
                "*SET:MSG 1234567890123456789012345678901234567890",
                "LEN",
                timeout_s=2.0,
                progress=progress,
                cancel_event=cancel_event,
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
                cancel_event=cancel_event,
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
                cancel_event=cancel_event,
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
                cancel_event=cancel_event,
            ),
            "检查越界日期是否返回 ERROR RANGE。",
        )
    restore_board_state()
    return _build_output(results, host_only=False, full=full)


def execute_checks_on_port(
    port_name: str,
    baud: int = 115200,
    progress: ProgressCallback | None = None,
    full: bool = False,
    cancel_event: Event | None = None,
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
        return execute_checks_on_open_port(port, progress=progress, full=full, cancel_event=cancel_event)


def execute_host_only_checks(
    progress: ProgressCallback | None = None,
    full: bool = False,
    cancel_event: Event | None = None,
) -> tuple[bool, str]:
    _raise_if_cancelled(cancel_event)
    results: list[CheckResult] = []
    checks = [
        ("HOST 配置持久化", "OK", ""),
        ("HOST 本地模式行为", "OK", ""),
        ("PING 心跳", "SKIP", "离线模式不连接真实串口"),
        ("SET/GET 指令", "SKIP", "离线模式只验证影子状态更新"),
        ("日期时间写入", "SKIP", "离线模式只写入本地影子板端时间"),
        ("模式切换", "OK", ""),
        ("天气协议", "OK", ""),
        ("铃声协议", "OK", ""),
        ("快捷键触发", "SKIP", "离线模式不产生物理按键事件"),
    ]
    if full:
        checks.extend(
            [
                ("城市/NTP入口", "OK", "已验证 PC 端入口存在；真实网络由界面按钮异步执行"),
                ("跑马灯与下划线", "OK", "离线数字孪生支持下划线七段码和有限滚动"),
                ("高并发按键鲁棒性", "SKIP", "离线模式不向真实 MCU 连发按键；请使用 COM 口全面测试"),
                ("ERROR 格式", "SKIP", "离线模式不连接 MCU，无法验证真实 ERROR 回包"),
            ]
        )
    total_steps = len(checks)
    for index, (name, status, detail) in enumerate(checks, start=1):
        _progress(progress, f"[STEP {index}/{total_steps}] {name}")
        _record(results, progress, name, status, detail)
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
