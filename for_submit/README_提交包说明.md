# 提交包说明

本目录用于模拟课程最终提交包。正式提交时请优先使用子目录：

```text
for_submit/大作业524031910102-陈云海/
```

该目录已经按课程要求整理为独立提交包，包含 MCU 工程、PC 上位机源码、Windows 打包版、项目实验报告 PDF、README、验收文档、截图素材和演示视频脚本。

## 正式提交目录

```text
大作业524031910102-陈云海/
├─ README.md
├─ 大作业524031910102-陈云海.pdf
├─ mcu/
│  ├─ clock.uvprojx
│  ├─ clock.uvoptx
│  ├─ src/main.c
│  ├─ obj/s800_clock.axf
│  ├─ obj/s800_clock.hex
│  └─ Driverlib/ Inc/ RTE/
├─ pc_host/
│  ├─ app.py
│  ├─ main.ui
│  ├─ ui_main.py
│  ├─ protocol.py
│  ├─ twin_widgets.py
│  ├─ requirements.txt
│  └─ assets/
├─ release/SmartClockHost-v2.2.zip
├─ docs/
├─ submission/
└─ demo/
```

## 交付状态

- MCU 工程：已提供 Keil5 工程、`main.c`、`s800_clock.axf` 和 `s800_clock.hex`；工程 `CreateHexFile=1`，输出名 `s800_clock`。
- PC 上位机：已提供源码、`.ui`、依赖说明和 v2.2 打包版。
- 项目报告：已提供 8 页单栏实验报告 PDF。
- 验证材料：`docs/大作业要求逐条验收对照.md` 记录课程要求逐项覆盖情况。
- 最新要求：`docs/FAQ_常见问题解析_V1.3(3).pdf` 已放入正式目录，协议细节以该版本为准。
- 演示视频：当前提供脚本和截图素材；最终提交前请补入实际录制 MP4。

注意：2026-06-17 已按 FAQ v1.3 重新编译 MCU 工程并刷新 `mcu/obj/s800_clock.axf` 与 `s800_clock.hex`；Keil 命令行烧录显示 `Programming Done`、`Verify OK`，随后 COM5 严格回归 `python pc_host/run_teacher_protocol_regression.py --port COM5` 为 `PASS=27 FAIL=0`。最终压缩提交前只需补入实际演示 MP4。

## 不应放入最终 zip 的内容

最终压缩包不需要包含 `.git/`、`.venv/`、`__pycache__/`、临时日志、PyInstaller 中间目录、Keil 中间 `.o/.d/.crf` 文件。当前正式提交目录已尽量整理为老师可直接阅读和运行的结构。
