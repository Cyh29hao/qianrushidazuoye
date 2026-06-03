# 调试记录

## 2026-06-02 环境与项目初始化
- 现象：题目 PDF 与安装指南都是扫描版，文本提取直接失败。
- 原因：PDF 主体是图片，`pypdf` 抽不到正文。
- 处理：先用 `pdftoppm` 转 PNG，再人工读取关键页。
- 验证：已确认 `§3 / §4.1 / §5 / §7 / §9` 的核心要求。

## 2026-06-02 Python GUI 依赖
- 现象：`pyqt5-tools` 在 `Python 3.12` 环境里解析依赖时失败。
- 原因：该包当前依赖链会回退到需要本地构建的旧版 `PyQt5`，并触发 `qmake` 缺失。
- 处理：改为稳妥方案，只安装 `PyQt5 + pyserial`，并用 `PyQt5.uic.pyuic` 生成 `ui_main.py`。
- 验证：`.venv` 中 `import PyQt5, serial` 成功。

## 2026-06-02 蜂鸣器映射确认
- 现象：实验模板和已有代码里没有现成蜂鸣器控制实现。
- 原因：课程实验阶段主要覆盖 GPIO/I2C/UART，没有进入大作业闹钟蜂鸣模块。
- 处理：回查 `TM4C_SUBBOARD_0414.pdf` 与 `TM4C1294XL user guide.pdf`。
- 结论：
  - 扩展板 `BEEP` 网名是 `PWM7`
  - LaunchPad `X11-75` 对应 `PK5 / M0PWM7`
  - 基础版先按普通 GPIO 输出驱动有源蜂鸣器
- 验证：原理图文本与 LaunchPad 引脚表已互相对上。

## 2026-06-02 目录骨架
- 已创建最终提交目录：
  - `mcu/`
  - `pc_host/`
  - `docs/`
- 已将官方 `EXP3` 模板拷贝并重定向到：
  - `mcu/src/main.c`
  - `mcu/obj/`
  - `mcu/listings/`

## 后续重点
- 用可编译/可运行为标准收敛 MCU `main.c`。
- 生成 `ui_main.py` 并验证 PC 上位机可启动。
- 联调阶段重点盯：
  - `FORMAT RIGHT`
  - `*EVT:DISP / *EVT:LED` 心跳上报
  - 本地编辑 `5 s` 超时退出
  - `USER1` 触发 PC 对时
