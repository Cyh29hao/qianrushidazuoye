# 智能联网时钟系统 v2.2 提交包入口

学生：陈云海
学号：524031910102
项目仓库：https://github.com/Cyh29hao/qianrushidazuoye

本目录是最终提交包的本地模拟目录。正式交给老师时，建议压缩其中的：

```text
大作业524031910102-陈云海/
```

并命名为：

```text
大作业524031910102-陈云海.zip
```

## 老师建议阅读顺序

1. 打开 `大作业524031910102-陈云海/大作业524031910102-陈云海.pdf`，先阅读 8 页项目实验报告。
2. 打开 `大作业524031910102-陈云海/README.md`，查看提交包目录、运行方法和验证证据。
3. 如需直接体验 PC 端，解压并运行 `release/SmartClockHost-v2.2.zip` 中的 `SmartClockHost.exe`。
4. 如需检查或重新烧录 MCU，打开 `mcu/clock.uvprojx`，自编主逻辑位于 `mcu/src/main.c`；Keil 工程已勾选 `Create HEX File`，输出名 `s800_clock`，烧录文件位于 `mcu/obj/s800_clock.axf` 与 `mcu/obj/s800_clock.hex`。

## 当前已放入的材料

- `大作业524031910102-陈云海/`：正式提交目录，包含源码、报告、说明、release 和演示素材。
- `大作业524031910102-陈云海.pdf`：与正式目录内相同的 8 页实验报告副本，便于快速打开。
- `release/SmartClockHost-v2.2.zip`：PC 上位机打包版，供本机或老师双击运行。
- `mcu/`：最新 MCU 工程副本。
- `pc_host/`：最新 PC 上位机源码副本。
- `docs/`、`submission/`、`demo/`：验收对照、实验报告源材料、截图和演示视频脚本。
- `docs/FAQ_常见问题解析_V1.3(3).pdf`：老师最新 FAQ，当前协议细节以该版本为准。

## 提交前最后一项

演示视频 MP4 需要由你按实际板卡演示录制后放入：

```text
大作业524031910102-陈云海/demo/演示视频.mp4
```

视频建议控制在 5 分钟内，覆盖：板端 RESET 开机、本地按键、PC 连接串口、数字孪生、NTP/天气、USER1/USER2、日程提醒、Matplotlib 看板和自动测试。

注意：2026-06-17 已按 FAQ v1.3 重新编译 MCU 工程，刷新正式目录中的 `mcu/obj/s800_clock.axf` 与 `s800_clock.hex`，并完成命令行烧录；Keil 日志显示 `Programming Done`、`Verify OK`，随后 `python pc_host/run_teacher_protocol_regression.py --port COM5` 结果为 `PASS=27 FAIL=0`。最终压缩提交前只需补入实际演示 MP4。
