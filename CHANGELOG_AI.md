# CHANGELOG_AI - AI 阶段变更记录

只记录阶段性最终修改、关键文件、验证结果和未解决问题；不记录无关对话。

## 2026-06-09

### 已完成修改

- 修复 PC 黑夜模式 UI：补齐状态栏/页脚、滚动条、下拉框弹出列表、复选框、信息条、表格、日志区和页面背景的深色主题规则。
- 保持全局按钮为统一蓝色主按钮风格，白天/黑夜均包含默认、悬停、按下、禁用状态。
- 主页数据看板保持 6 张卡片式布局；串口状态保留在“串口连接”模块，不再放回数据看板。
- 删除当前界面和最终说明文档中的 OTA 预留/占位露出。
- 新增 `AGENT.md`，记录后续 AI 必须遵守的 UI 主题、布局、OTA、验证和 Git 规则；`AGENTS.md` 已提示先读 `AGENT.md`。
- 更新 `PROJECT_CONTEXT.md`，记录当前最终状态和本轮验证结果。

### 关键文件

- `pc_host/app.py`
- `AGENT.md`
- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`
- `docs/extensions-roadmap.md`
- `docs/host-2.0-architecture.md`

### 验证结果

- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/twin_widgets.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- Windows 原生 Qt 平台截图检查通过：主页、系统设置、闹钟与日程管理、调试与测试在白天/黑夜模式下均无可见 OTA，按钮未丢失蓝色主样式。
- 程序化检查通过：四个页面白天/黑夜可见按钮均有样式；黑夜模式状态栏前景色为浅色、背景为深色。

### 未解决问题

- MCU 端改动仍需 Keil5 正式编译、烧录与实板验证。
- 如果提交/展示使用的是已打包 `.exe`，需要重新打包 PC 端以包含本轮 UI 修复。

## 2026-06-08

### 已完成修改

- 确认真正主版本为 `D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\真正的最新版`，并把接手上下文写入 `PROJECT_CONTEXT.md`、开发规则写入 `AGENTS.md`。
- 迁入并记录老师资料目录 `docs/大作业要求/`，包含正式题目、安装指南、FAQ、MCU 与 PC 端开发要求。
- 修正 `USER1` 功能：MCU 短按 `USER1` 只上报 `*EVT:KEY USER1`；PC 收到后触发 NTP 对时；MCU 长按 `USER1` 保留 DAY/NIGHT 一键切换。
- PC 端保留并明确 `DAY/NIGHT 切换` 按钮；虚拟 `USER1` 改为触发 PC 侧 NTP 对时。
- 补强未连接/本地模式数字孪生：根据影子板端时间、`DISPLAY`、`FORMAT`、`MODE` 持续生成 7SEG/LED 画面；`USER2` 可短显缓存天气。
- 更新 `.gitignore`，排除根目录 `.venv/` 和历史对话材料，避免误上传。

### 关键文件

- `mcu/src/main.c`
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `.gitignore`
- `PROJECT_CONTEXT.md`
- `AGENTS.md`
- `CHANGELOG_AI.md`

### 验证结果

- `python -m py_compile` 通过 PC 关键 Python 文件语法检查。
- 离线显示帧 helper 断言通过：覆盖 `LEFT`、`RIGHT`、影子时间推进、`NIGHT`、`DISPLAY OFF`。
- PyQt 离屏烟测通过：虚拟 `USER1` 会进入 PC 侧 NTP 对时入口，离线数字孪生可刷新显示帧。
- `execute_host_only_checks()` 直接调用返回 `PASS`。

### 未解决问题

- 本机未发现 Keil/ARM 编译器，MCU 改动尚未进行 Keil 编译、烧录和真板验证。
- `pc_host/run_extension_checks.py` 内部有 host-only 检查函数，但 CLI 入口尚未暴露 `--host-only` 参数。
- 日程 UI 文案与按钮显隐仍需后续收口。

### GitHub 备份前状态更新

- MCU：修正 7SEG 物理位编码，开机全亮帧为 `88888888 FF`，补齐 `*EVT:DISP`/`*EVT:LED` 1Hz 全量心跳，长按阈值调整到 800ms，`USER1` 短按请求 PC 对时、长按切换 DAY/NIGHT。
- PC：离线数字孪生按同一 7SEG 物理位规则显示；READY 后播放板端开机镜像帧；收到 `*EVT:KEY` 后高亮虚拟按键；网络模块合并定位/保存到一键执行；日程启停只保留双击入口，语音文本留空则不播报。
- 自动测试：界面和 CLI 增加预计耗时、逐项 OK/FAIL/SKIP、失败排查提示，并支持 `--host-only`。
- 网络/NTP/天气：保留前阶段 NTP 多源兜底、HTTP Date 兜底、国内天气源 + Open-Meteo/wttr 多源兜底，兼容开/不开代理环境。
- 忽略规则：继续排除 `.venv/`、运行日志/状态、Keil 编译中间目录、历史对话材料，并新增忽略 Keil 本机 GUI 配置 `mcu/*.uvgui*`、`mcu/*.uvguix*`。

### 备份前验证

- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/twin_widgets.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过。
- 7SEG 映射断言通过：`12.30.45 LEFT -> 12_30_45 / 24`，`RIGHT -> 54_03_21 / 24`。
- MCU `gcc -fsyntax-only -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。

### 当前未解决问题

- 仍需用 Keil5 打开 `mcu/clock.uvprojx` 进行正式编译、烧录与实板验证。
- 当前 `.venv` 缺 PyQt5，GUI 离屏/真实界面烟测需要在完整 PC 环境中运行。
