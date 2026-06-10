# 智能联网时钟系统提交材料准备说明

依据：`docs/大作业要求/大作业题目-学生版_V1.2.pdf` 第 7 节“提交要求”。

当前学生信息按项目已有命名推定：

- 学号：`524031910102`
- 姓名：`陈云海`
- 最终压缩包建议名：`大作业524031910102-陈云海.zip`
- 压缩包内顶层目录建议名：`大作业524031910102-陈云海/`
- 简介 PDF 建议名：`大作业524031910102-陈云海.pdf`

## 1. 最终提交目录结构

```text
大作业524031910102-陈云海/
├─ mcu/
│  ├─ Inc/
│  ├─ Driverlib/
│  ├─ RTE/
│  ├─ src/
│  │  └─ main.c
│  ├─ obj/
│  │  └─ s800_clock.axf
│  ├─ clock.uvprojx
│  └─ clock.uvoptx
├─ pc_host/
│  ├─ app.py
│  ├─ bootstrap_qt.py
│  ├─ diagnose_qt_runtime.py
│  ├─ extension_services.py
│  ├─ extension_store.py
│  ├─ protocol.py
│  ├─ run_extension_checks.py
│  ├─ twin_widgets.py
│  ├─ ui_main.py
│  ├─ main.ui
│  ├─ requirements.txt
│  ├─ launch_pc_host.bat
│  ├─ launch_pc_host.ps1
│  ├─ config.json
│  └─ assets/
├─ docs/
│  ├─ 大作业524031910102-陈云海.pdf
│  ├─ 演示视频.mp4
│  ├─ test-guide.md
│  ├─ 当前项目状态总览.md
│  └─ 自主答辩准备初稿.md
└─ README.md
```

可选材料：

- `build_release/SmartClockHost-v2.1.zip`：PC 打包版，便于老师双击运行。它不是老师题目里的必交源码项，若 Canvas 容量允许，可以放入 `docs/release/` 或在 README 里说明本地路径。
- `docs/大作业要求逐条验收对照.md`：给自己答辩用的逐项对照，最终提交可放入 `docs/`，但不一定替代简介 PDF。

## 2. 必交清单对照

| 老师要求 | 当前项目位置 | 当前状态 |
| --- | --- | --- |
| 全部 S800 板与 PC 端源代码，含 `.ui` | `mcu/`、`pc_host/` | 已具备。 |
| MCU 可烧写文件 `obj/xxx.axf` | `mcu/obj/s800_clock.axf` | 已存在；昨晚修改 MCU 后，正式提交前仍建议 Keil5 重新编译确认。 |
| PC 端 `requirements.txt` | `pc_host/requirements.txt` | 已具备，包含 PyQt5 与 pyserial。 |
| 简介 PDF，4-8 页 | 待生成：`docs/大作业524031910102-陈云海.pdf` | 已有大纲，尚未排版导出。 |
| 演示视频，≤5 分钟带旁白 | 待录制：`docs/演示视频.mp4` | 已有脚本大纲，尚未录制。 |
| 根目录 `README.md`，说明编译/运行命令 | `README.md` | 已更新到 v2.1 状态。 |

## 3. 打包前清理规则

最终提交 zip 中应排除：

- `.git/`
- `.venv/`
- `__pycache__/`
- `pc_host/logs/`
- `tmp/`
- `build_release/_pyi_build/`
- `build_release/_pyi_dist/`
- Keil 中间文件，如 `*.o`、`*.d`、`*.crf`、`*.lst`、`*.map`，除非老师明确要求保留完整编译目录

最终提交 zip 中应保留：

- `mcu/Inc/`、`mcu/Driverlib/`、`mcu/RTE/`
- `mcu/src/main.c`
- `mcu/clock.uvprojx`、`mcu/clock.uvoptx`
- `mcu/obj/s800_clock.axf`
- `pc_host/*.py`、`pc_host/*.ui`、`pc_host/requirements.txt`、`pc_host/assets/`
- 根 `README.md`
- 简介 PDF 和演示视频

## 4. 简介 PDF 建议页数

建议做 6 页，满足老师 4-8 页要求：

1. 项目目标与系统总览
2. MCU 端设计：时基、显示、按键、闹钟
3. 串口协议与 PC 协同
4. 数字孪生与网络扩展
5. 自主增加功能：多日程/课程提醒系统
6. 难点、稳定性保护、测试与演示流程

## 5. 演示视频建议结构

总时长控制在 4 分 30 秒左右：

1. 0:00-0:30 开机动画、版本号、基础时钟
2. 0:30-1:20 板端本地按键、日期/周几/格式/速度/闹钟
3. 1:20-2:10 PC 连接串口、数字孪生同步、协议日志
4. 2:10-3:00 NTP、天气、USER1、USER2、自动昼夜
5. 3:00-3:50 多日程提醒与铃声/语音逻辑
6. 3:50-4:30 自动测试、异常恢复、总结亮点

## 6. 提交前必须再做的事

1. Keil5 重新 Build，确认 `mcu/obj/s800_clock.axf` 是最新源码产物。
2. 烧录实板，按 `docs/test-guide.md` 跑一遍关键场景。
3. 用真实 COM5 测：连接自动 NTP、RESET 自动 NTP、USER1 连击、USER2 天气短显、跑马灯、日程触发。
4. 打开打包版 `build_release/SmartClockHost-v2.1/SmartClockHost.exe` 做一次白天/黑夜 UI 检查。
5. 生成简介 PDF 和演示视频，并按命名规则放入 `docs/`。
6. 按本文件目录结构复制一份干净提交目录，再压缩为 `大作业524031910102-陈云海.zip`。
