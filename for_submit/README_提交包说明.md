# 智能联网时钟系统 for_submit 提交模拟目录

本目录用于模拟最终提交包结构，后续可按老师命名规则整理为正式 zip。当前仍是准备目录，不等同于最终提交包。

## 当前版本

- 主版本：v2.2
- PC 打包版：`release/SmartClockHost-v2.2.zip`
- MCU 工程副本：`mcu/`
- 主工程仓库：`https://github.com/Cyh29hao/qianrushidazuoye`

## 建议最终提交结构

```text
大作业524031910102-陈云海/
├─ README.md
├─ mcu/
├─ pc_host/
├─ docs/
├─ submission/
├─ release/
│  └─ SmartClockHost-v2.2.zip
└─ demo/
   ├─ 演示视频_5分钟脚本初稿.md
   ├─ 演示视频素材说明.md
   └─ screenshots/
```

## 本目录已准备内容

- `提交前检查清单.md`：最终打包、烧录、录视频前逐项核对。
- `答辩亮点速记.md`：自主答辩时重点讲的创新点。
- `docs/`：验收对照、状态总览、自主答辩准备初稿等文档副本。
- `submission/`：项目交付说明 PDF、LaTeX 源文件、演示视频脚本和提交说明副本。
- `demo/screenshots/`：PC 界面截图素材。
- `release/`：本地 v2.2 PC 打包 zip，不提交 Git。
- `mcu/`：本地 MCU 工程副本，不提交 Git。

## 还需要你亲自完成

1. 录制不超过 5 分钟演示视频，建议按 `submission/演示视频_5分钟脚本初稿.md` 执行。
2. 按老师要求命名最终 zip。
3. 提交前可选复核：COM5 跑老师 testcmdV2.1 或 `pc_host/run_teacher_protocol_regression.py --port COM5`，确认仍无 TIMEOUT。

已完成的硬件/串口证据：Keil5 最新工程 `0 Error(s), 0 Warning(s)`，COM5 烧录 `Verify OK`，老师 testcmdV2.1 原始测试 `OK 92`、`ERROR 8`、`TIMEOUT 0`、评分 `10.0/10.0`。

已完成的提交材料：项目交付说明 PDF `大作业524031910102-陈云海.pdf` 已重做为 14 页单栏教师阅读版，内容可独立说明系统使用方式、总体架构、MCU 端、PC 上位机、串口协议、扩展亮点、测试证据和最终交付成果。
