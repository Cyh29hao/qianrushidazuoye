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

- `build_release/SmartClockHost-v2.2.zip`：PC 打包版，便于老师双击运行。它不是老师题目里的必交源码项，若 Canvas 容量允许，可以放入 `docs/release/` 或在 README 里说明本地路径。
- `docs/大作业要求逐条验收对照.md`：给自己答辩用的逐项对照，最终提交可放入 `docs/`，但不一定替代简介 PDF。

## 2. 必交清单对照

| 老师要求 | 当前项目位置 | 当前状态 |
| --- | --- | --- |
| 全部 S800 板与 PC 端源代码，含 `.ui` | `mcu/`、`pc_host/` | 已具备。 |
| MCU 可烧写文件 `obj/xxx.axf` | `mcu/obj/s800_clock.axf` | 已用 Keil5 重新编译最新工程，构建 `0 Error(s), 0 Warning(s)`，COM5 烧录校验 `Verify OK`。 |
| PC 端 `requirements.txt` | `pc_host/requirements.txt` | 已具备，包含 PyQt5、pyserial 与 matplotlib。 |
| 简介 PDF / 项目交付说明 PDF | `submission/大作业524031910102-陈云海.pdf` | 已重做为 14 页单栏教师交付文档；内容独立说明系统使用方式、总体架构、MCU 端、PC 上位机、串口协议、扩展亮点、测试证据和最终交付。已同步保存 `.tex`。 |
| 演示视频，≤5 分钟带旁白 | 待录制：`docs/演示视频.mp4` | 已有脚本大纲，尚未录制。 |
| 根目录 `README.md`，说明编译/运行命令 | `README.md` | 已更新到 v2.2 状态。 |

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

## 4. 项目交付说明 PDF 当前结构

当前 PDF 已按“老师不先读 README、不先运行程序也能看懂项目”的目标彻底重做，采用单栏正式文档样式，共 14 页。它不是答辩提纲，而是面向课程交付的项目说明书：

1. 项目入门：系统是什么、怎样使用；
2. 课程要求与项目完成范围；
3. 总体架构：硬件端、PC 端与协议协同；
4. S800/TM4C1294 板端设计；
5. PC 上位机设计；
6. 串口协议与双向同步；
7. 扩展功能与自主设计亮点；
8. 测试验证与完成度；
9. 最终提交目录与运行说明；
10. 总结。

## 5. 演示视频建议结构

总时长控制在 4 分 30 秒左右：

1. 0:00-0:30 开机动画、版本号、基础时钟
2. 0:30-1:20 板端本地按键、日期/周几/格式/速度/闹钟
3. 1:20-2:10 PC 连接串口、数字孪生同步、协议日志
4. 2:10-3:00 NTP、天气、USER1、USER2、自动昼夜
5. 3:00-3:50 多日程提醒与铃声/语音逻辑
6. 3:50-4:30 自动测试、异常恢复、GitHub 版本管理与总结亮点

## 6. 提交前必须再做的事

1. 录制演示视频，并按命名规则放入 `docs/`。
2. 按本文件目录结构复制一份干净提交目录，再压缩为 `大作业524031910102-陈云海.zip`。
3. 提交前可快速复核：交付 PDF 为 14 页且可正常打开；老师 testcmdV2.1 评分文件为 `10.0/10.0`、`TIMEOUT 0`；打包版 `SmartClockHost-v2.2.exe` 能打开；README 与协议速查文档能解释 8 个预期 `ERROR`。
