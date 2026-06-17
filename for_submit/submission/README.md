# 项目实验报告与演示材料说明

本目录保存智能联网时钟系统 v2.2 的报告源材料和演示脚本。正式评阅时优先打开正式提交包：

```text
for_submit/大作业524031910102-陈云海/
```

## 当前准稿

正式准稿是 8 页单栏实验报告：

```text
for_submit/大作业524031910102-陈云海/大作业524031910102-陈云海.pdf
```

正式评阅以 `for_submit/大作业524031910102-陈云海/大作业524031910102-陈云海.pdf` 为准。`*_初稿.pdf`、`*_预览.pdf` 和历史说明稿仅作过程归档，不用于最终提交。

## 源文件

- `大作业524031910102-陈云海_8页版Markdown.md`：与正式 PDF 对应的 Markdown 源稿。
- `pdf_report_style.css`：Markdown 转 PDF 时使用的样式。
- `design_intro_assets/`：报告截图、板卡照片和界面素材。
- `演示视频_5分钟脚本初稿.md`：录制 5 分钟内演示视频时可参考的顺序。

## 最新 FAQ v1.3 状态

老师说明“FAQ 以最新版本为准”。当前 PC 端、文档和 MCU 源码已按 `FAQ_常见问题解析_V1.3(3).pdf` 对齐：`*EVT:DISP` 的 8 字符不包含小数点，空位用 `_`，`dpHex` 的 bit0 对应最左 digit；例如 `12.30.45` LEFT 为 `123045__ 0A`，RIGHT 为 `__540321 28`。

2026-06-17 已用 Keil5 重新编译并烧录最新 MCU 工程，刷新 `mcu/obj/s800_clock.axf` 和 `s800_clock.hex`；Keil 下载日志显示 `Programming Done`、`Verify OK`。随后复跑：

```powershell
python pc_host/run_teacher_protocol_regression.py --port COM5
```

结果为 `PASS=27 FAIL=0`，其中 FAQ v1.3 实板 LEFT/RIGHT 显示帧已分别通过 `123045__ 0A` 与 `__540321 28` 检查。

## 最终还需补充

1. Keil5 重新编译并烧录最新 MCU 工程。
2. 录制实际板卡演示 MP4，并放入正式提交包：

```text
for_submit/大作业524031910102-陈云海/demo/演示视频.mp4
```
