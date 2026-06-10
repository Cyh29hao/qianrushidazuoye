# 简介 PDF 初稿大纲

目标文件名：`docs/大作业524031910102-陈云海.pdf`

页数建议：6 页。老师要求 4-8 页，且要包含框图、关键代码片段、亮点说明、难点解决、`§4.3` 自主增加功能的“动机-设计-实现-演示”。

## 第 1 页：项目目标与总体架构

标题：智能联网时钟系统

应写内容：

- 利用 S800 板与 PC 上位机共同搭建智能数字时钟。
- 板端可离线独立运行，PC 可远程控制、状态监控、数字孪生镜像。
- 总体模块：
  - MCU：SysTick、显示、按键、闹钟、串口协议。
  - PC：串口管理、控制面板、数字孪生、日志、网络服务、日程。
  - 协议：`*SET`、`*GET`、`*EVT`、`*PING`。

建议图：

```text
S800 板
  SysTick/7SEG/LED/Keys/Buzzer
        ^
        | USB 虚拟串口 ASCII 协议
        v
PC 上位机
  控制面板/数字孪生/日志/NTP/天气/日程/自动测试
```

## 第 2 页：MCU 端基础功能设计

应写内容：

- 1 ms SysTick 只做节拍计数，主循环消费 10 ms、100 ms、500 ms、1000 ms 任务。
- 7SEG 动态扫描和显示帧缓存。
- 日期/时间/闹钟状态机。
- FUNC/SHIFT/ADD/SAVE 编辑逻辑，5 秒无操作退出。
- 闹钟蜂鸣不超过 10 秒，FUNC 可停止。

关键代码位置：

- `mcu/src/main.c`
- `Tick10ms()`、`Tick1000ms()`
- `RefreshDisplayAndLeds()`
- `HandleKeyPress()`
- `StartAlarmRing()`、`ServiceBuzzer()`

## 第 3 页：串口协议与容错

应写内容：

- 115200, 8N1, ASCII 行协议。
- 支持大小写不敏感、空格容错、合法缩写。
- 支持 `*RST`、`*SET:*`、`*GET:*`、`*PING`、`*EVT:*`。
- 错误统一返回 `ERROR <reason>`。
- `FORMAT RIGHT` 下 `GET` 与显示同步逆序，小数点使用 `dpHex` 保持物理位一致。
- PC 侧 NTP 写入采用 `SET DATE -> OK -> SET TIME -> OK`，避免命令过密造成板端状态卡死。

关键代码位置：

- MCU：`UART_ProcessLine()`、`HandleSetDate()`、`HandleGet()`、`EmitDisplayEvent()`
- PC：`pc_host/protocol.py`、`pc_host/app.py::send_command()`、`_handle_sync_write_ok()`

## 第 4 页：PC 上位机与数字孪生

应写内容：

- PyQt5 + pyserial 实现上位机。
- 左侧主功能区：主页、系统设置、闹钟与日程、调试与测试。
- 右侧数字孪生：8 位 7SEG、8 位 LED、SW1-SW8、USER1/USER2。
- 串口连接时数字孪生优先映射板端真实 `*EVT:DISP`/`*EVT:LED`。
- 未连接串口时可本地模拟演示。
- 日志区记录 TX/RX/INFO/WARN/ERR，自动测试逐项输出 OK/FAIL/SKIP。

关键代码位置：

- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `pc_host/run_extension_checks.py`

## 第 5 页：扩展功能与自主增加功能

扩展功能：

- E1 网络对时：NTP/HTTP Date 兜底，USER1 可触发。
- E2 天气获取：Open-Meteo 路线，USER2 天气短显，没有天气显示 `NO WX`。
- E3 自动昼夜：根据城市/时区/天气日出日落判断 DAY/NIGHT。
- E4 数据看板：采用主页卡片和事件日志的轻量看板方案。

自主增加功能：

- 多日程/课程提醒系统。
- 支持单次日期提醒、每周提醒、启用/停用、铃声、板端标签、语音文本。
- 语音文本留空默认不播报。
- 日程触发时写日志、下发板端短显、触发铃声。

必须写清“四段说明”：

- 动机：基础闹钟只能表达单一时间，课程/会议/答辩准备需要多条提醒。
- 设计：PC 持久化日程表，按当前城市时间每秒检查；板端只负责短显和蜂鸣。
- 实现：`ScheduleItem`、`schedule_trigger_matches()`、`trigger_schedule()`。
- 演示：新增一个当前时间 +1 分钟的单次提醒，触发后看板端和 PC 日志。

## 第 6 页：难点、稳定性与测试

应写内容：

- 难点 1：7SEG 小数点物理位与 RIGHT 逆序。
- 难点 2：RESET/连接/NTP/天气/协议测试并发，容易抢串口。
- 难点 3：USER1 连续短按/长按容易触发 NTP 和模式切换风暴。
- 解决：
  - PC 串口互斥、NTP token、weather timeout、serial watchdog。
  - MCU USER1 冷却、I2C 忙等上限、消息状态机超时恢复。
  - 自动测试快速/全面两档。

建议截图/素材：

- PC 主界面白天和黑夜模式各一张。
- 数字孪生和实物同屏。
- 自动测试输出 PASS。
- USER2 天气短显。
- 日程触发日志。

最后一句总结：

本系统已覆盖基础要求、PC 协同、三项主要扩展功能和自主日程提醒功能，目标是作为可演示、可调试、可答辩的完整智能联网时钟系统。
