# PROJECT_CONTEXT - 智能时钟联网系统

最后更新：2026-06-09（v2.1 UI/release 收口）

本文件给新的 AI 对话快速接手使用，只记录当前最终状态、目录结构、要求、已实现功能、已知问题和下一步计划。不要把聊天记录、推理过程或临时争论写进来。后续以最新代码和本文件为准；旧任务文档可能已经过时，需要复核当前实现。

## 当前主版本

- 项目根目录：`D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\真正的最新版`
- 当前 Git 分支：`gemini-ui-improvements`
- 远端仓库：`https://github.com/Cyh29hao/qianrushidazuoye`
- 当前主版本判断：`真正的最新版` 是 Gemini CLI 修改后的真正最新版。此前误认为 `大作业524031910102-陈云海-最新版` 是主版本；该目录现在只作参考来源/旧分支，不要继续在那里开发。
- 父目录中的 `大作业524031910102-陈云海-0604`、`backups`、`524031910102陈云海` 等视为旧版/备份，除非用户明确要求，不要删除或回退。
- 当前工作树重要状态：
  - `mcu/src/main.c` 已把 `USER1` 短按改为只上报 `*EVT:KEY USER1`，由 PC 触发 NTP 对时；`USER1` 长按保留板端一键 DAY/NIGHT 切换并上报 `*EVT:MODE`。
  - `pc_host/app.py` 保留 Gemini 改动：左侧菜单顺序调整；新增 `toggle_schedule_enabled()`；日程表 `itemDoubleClicked` 已连接到双击启停。
  - `pc_host/app.py` 已补强离线数字孪生：未连接板端时根据 `runtime_state.json` 的影子板端时间、`DISPLAY`、`FORMAT`、`MODE` 生成当前应显示的 7SEG/LED；影子时钟会按秒推进；`USER2` 可短显缓存天气。
  - `pc_host/twin_widgets.py` 已把虚拟 `USER1` 标记为 NTP 用途；点击虚拟 `USER1` 会触发 PC 侧 NTP 对时。上位机 `MODE` 按钮文案为 `DAY/NIGHT 切换`，保留一键切日夜入口。
  - 已比对误认旧目录与真版本的关键源码：除 `pc_host/app.py` 外，`mcu/src/main.c`、`pc_host/protocol.py`、`extension_services.py`、`extension_store.py`、`run_extension_checks.py`、`twin_widgets.py`、`requirements.txt`、`README.md` 和主要 docs 哈希一致。不要用误认旧目录的 `pc_host/app.py` 覆盖真版本。
  - `.gitignore` 已排除根目录 `.venv/`、历史对话材料、运行缓存、Keil 本机 GUI 配置和编译中间目录，避免误传。
  - `docs/dialogue_with_codex.md` 和 `docs/summary_dialogue_with_codex.md` 是 Gemini/历史对话材料，已忽略；不要把它们当作正式报告。
  - `PROJECT_CONTEXT.md`、`AGENTS.md`、`AGENT.md`、`CHANGELOG_AI.md`、`docs/大作业要求/` 是接手/规则/变更记录/课程资料，准备纳入 GitHub 上传候选。
  - 2026-06-09 UI 收口：PC 黑夜模式已补齐状态栏/页脚、滚动条、下拉框弹出列表、复选框、日志区、信息条和表格的深色主题；按钮保持统一蓝色主按钮；主页数据看板保持 6 张卡片布局；串口状态保留在“串口连接”模块。
  - 2026-06-09 v2.1 收口：`APP_VERSION` 与 `pc_host/config.json` 已更新到 2.1；左侧主内容和右侧数字孪生使用稳定 `QSplitter` 分栏，当前按用户机器常见 1280x720/1366x768 窗口复核，左栏收窄到约 460px、右栏优先保留数字孪生完整宽度；系统设置、网络对时、铃声、闹钟、日程、自动测试等页面统一行高/边距，左侧滚动页 host 不再宽过 viewport，避免控件缺右边界或被右侧面板遮挡。
  - v2.1 下拉框修复：主要 `QComboBox` 改为只读可点击、当前文本居中；串口下拉框仍可手动输入 `COM5`；下拉箭头改为实色背景 XPM，避免透明背景在部分 Qt/Windows 环境中显示为黑色小方块。
  - v2.1 主页/日志小修：主页改为无滚动条一屏布局，数据看板为 3 列 x 2 行；“日志与异常”不再使用骑线的 `QGroupBox` 标题，改为框内标题。
  - v2.1 调试页时间模块小修：`时间与同步` 改为 `时间写入与 NTP 对时`；上半区明确为手动编辑日期/时间后写入 S800，下半区明确为 NTP 网络对时并写入 S800，并压缩了 NTP 按钮与上方控件之间的间隔。
  - OTA 已从当前 PC 界面和最终说明文档中移除；历史聊天归档中可能仍出现 OTA 旧讨论，但不作为当前功能。

## 目录结构

```text
.
├─ docs/
│  ├─ 大作业要求/
│  │  ├─ 大作业题目-学生版_V1.2.pdf
│  │  ├─ 大作业-软件安装指南_V1.1(2).pdf
│  │  ├─ FAQ_常见问题解析_V1.0(1).pdf
│  │  └─ MCU与PC端开发要求_V1.0(1).pdf
│  ├─ deployment.md
│  ├─ test-guide.md
│  ├─ tech-notes.md
│  ├─ debug-log.md
│  ├─ gemini-ui-summary.md
│  ├─ dialogue_with_codex.md
│  ├─ summary_dialogue_with_codex.md
│  ├─ host-2.0-architecture.md
│  ├─ host-2.0-behavior-map.md
│  ├─ host-2.0-known-gaps.md
│  ├─ next-step-ui-and-host-fixes.md
│  └─ handoff-for-gemini-or-new-codex.md
├─ mcu/
│  ├─ Driverlib/
│  ├─ Inc/
│  ├─ RTE/
│  ├─ src/main.c
│  ├─ clock.uvprojx
│  └─ clock.uvoptx
├─ pc_host/
│  ├─ app.py
│  ├─ main.ui
│  ├─ ui_main.py
│  ├─ protocol.py
│  ├─ twin_widgets.py
│  ├─ extension_services.py
│  ├─ extension_store.py
│  ├─ run_extension_checks.py
│  ├─ config.json
│  ├─ schedules.json
│  ├─ requirements.txt
│  └─ assets/
├─ README.md
├─ PROJECT_CONTEXT.md
├─ CHANGELOG_AI.md
├─ AGENT.md
└─ AGENTS.md
```

## 老师要求与优先级

主依据是 `docs/大作业要求/大作业题目-学生版_V1.2.pdf`。老师补充的 `FAQ_常见问题解析_V1.0(1).pdf` 和 `MCU与PC端开发要求_V1.0(1).pdf` 只是基本参考，不能推翻现有方案，但要用于查缺补漏。

注意：`大作业题目-学生版_V1.2.pdf` 当前用 `pypdf` 抽取每页文本长度为 0，基本可视为扫描/图片式 PDF。需要精确引用正式题目时，应渲染页面或 OCR 复核，不要假装已经从 PDF 文本直接读到原文。

正式目标：

- S800 板必须在不连接 PC 时独立完成本地时钟、日期、闹钟、按键设置、显示、LED、蜂鸣等基础功能。
- PC 上位机通过 USB 虚拟串口与 S800 双向协同，实现远程控制、状态监控、日志、异常处理和 1:1 数字孪生镜像。
- 串口协议必须统一、容错可靠，支持 `*RST`、`*SET:*`、`*GET:*`、`*PING` 和 `*EVT:*` 主动上报。
- PC 数字孪生必须体现 8 位 7SEG、8 位 LED、8 个 I2C 按键、USER1/USER2，并与板端双向同步。
- 总分 100，其中基础 S800、协议完整性、PC 协同是核心；扩展功能和自主增加功能影响高分。
- A/A+ 门槛要求自主增加功能得分大于 0、扩展功能至少完成 2 项，并自我报名参加答辩且通过。

新增参考文件重点：

- 推荐环境倾向 Python 3.11.9、PyQt5、pyserial；当前工程已有 Python 3.12/PyQt5 可运行路线，后续提交说明需解释或调整。
- `*EVT:DISP` 推荐格式为 `*EVT:DISP <8字符> <dpHex>`。
- `FORMAT RIGHT` 下，数码管显示、`*GET` 应答和 `*EVT:DISP` 都要同步逆序，小数点跟随规则必须正确。
- `USER1` 按键应触发 PC 对时链路，`USER2` 用于天气短显；这一点要重点复核当前实现。
- MCU 自编逻辑应集中在 `mcu/src/main.c`；底层公共库保留在 `Inc/`、`Driverlib/`。

## 已实现功能

### MCU 端

核心文件：`mcu/src/main.c`

已实现：

- `SysTick` 1 ms 时基与软节拍。
- I2C 七段数码管动态显示、8 位 LED、8 个 I2C 按键、USER1/USER2 GPIO。
- 时间、日期、年份显示，闰年/月末/23:59:59 进位逻辑。
- LEFT/RIGHT 流水显示与两档速度。
- 本地闹钟、蜂鸣器、响铃超时与 FUNC 停止响铃。
- 本地编辑状态机：日期、时间、闹钟；SHIFT/ADD/SAVE/FUNC 长按；5 秒无操作退出。
- `USER1` 短按请求 PC 对时；`USER1` 长按保留板端 DAY/NIGHT 一键切换；`USER2` 短按显示缓存天气。
- UART 行协议解析、大小写/空格容错、错误响应。
- 指令：`*RST`、`*SET:DATE/TIME/ALARM/DISPLAY/FORMAT/MSG/BEEP/LED/KEY/MODE/WEATHER/RING`、`*GET:DATE/TIME/ALARM/DISPLAY/FORMAT/MODE`、`*PING`。
- 主动事件：`*EVT:DISP`、`*EVT:LED`、`*EVT:MODE`、`*EVT:KEY`、`*EVT:ALARM`、`*EVT:ALARM_OFF`、`*EVT:EDIT`。
- 7SEG 物理位格式已按老师 FAQ/图片修正：`12.30.45` 上报为 `12_30_45 24`，RIGHT 为 `54_03_21 24`。
- 开机全亮帧为 `88888888 FF`，LED 在开机画面全亮/全灭阶段同步；`*EVT:DISP` 与 `*EVT:LED` 每秒全量心跳。

### PC 端

核心文件：`pc_host/app.py`

已实现：

- PyQt5 上位机主界面，左侧折叠菜单，右侧数字孪生与日志/异常区。
- 串口扫描、连接、断开、PING/PONG 延迟显示。
- `不使用串口` 本地模式：未连接板端时仍可更新本地配置、运行态和模拟状态。
- 控制面板：日期、时间、闹钟、显示开关、FORMAT、MODE、蜂鸣、LED、消息、虚拟按键、原始命令。
- 数字孪生镜像：七段数码管、LED、虚拟按键，由 `twin_widgets.py` 绘制。
- 未连接/本地模式下，数字孪生会按影子板端时间和本地运行态持续显示当前应有画面；`DISPLAY OFF` 显示全灭，`FORMAT RIGHT` 逆序显示，`NIGHT` 显示时分。
- PC 收到 `*EVT:KEY USER1` 或点击虚拟 `USER1` 时，会走 NTP 对时并写入板端；未连接时写入本地影子状态。PC 端 `MODE` 按钮保留 `DAY/NIGHT 切换`。
- NTP 对时、天气刷新、地点槽位、自动昼夜、主题跟随、语音播报开关。
- 网络对时与天气模块已合并“定位城市/保存地点”到“一键对时、刷新天气并应用”，开始时立即写日志提示。
- 多日程提醒、板载单次闹钟、铃声类型、事件持久化。
- 日程新增默认启用；启停入口保留为双击日程项；语音文本留空则不播报。
- 数据看板：连接/显示、城市/时间、天气、昼夜模式、日程统计、下次提醒。
- 主页数据看板已改为卡片式布局：当前时间、日期与星期、城市/天气、昼夜模式、下次提醒、系统状态；串口状态不再放入数据看板。
- 白天/黑夜主题已统一覆盖主窗口背景、分组框、卡片、输入框、下拉框、表格、日志区、滚动条、状态栏/页脚和按钮状态。
- 调试与测试页已删除 OTA 预留模块。
- 自动化检查入口：真串口检查与 `--host-only` 离线检查；界面/CLI 可显示预计耗时、逐项 OK/FAIL/SKIP 和失败排查提示。
- PC 数字孪生已支持 READY 后本地播放同款开机镜像帧，并在收到真实 `*EVT:DISP` 后切回实物跟随；`*EVT:KEY` 会高亮对应虚拟按键约 200ms。
- 持久化：`config.json`、`schedules.json`、`runtime_state.json`、`logs/events.jsonl`。

## 当前已知问题

这些是基于 2026-06-08 当前代码扫描得到的状态，继续开发前应再次以最新代码复核。

- 日程管理双击启停已在真正最新版中接线：`scheduleTable.itemDoubleClicked.connect(self.toggle_schedule_enabled)`。当前主菜单实际使用 `_build_alarm_schedule_page()` -> `_build_schedule_management_group()`，该活动页面已移除 `清空表单` 按钮；但废置的 `_build_schedule_dashboard_page()` 中仍残留 `scheduleResetButton` 代码，后续重构时注意不要误判。
- 日程 UI 文案仍需收口：当前活动代码仍是 `单次日期`、`新增 / 更新提醒`，未完全变成旧任务书要求的 `单次执行`、按选中状态显示 `新增提醒/更新提醒/删除提醒`。
- `pc_host/requirements.txt` 目前只有 PyQt5 与 pyserial，和新增参考文件建议的 Python 3.11.9、PyQt5 5.15.9、ntplib/requests/astral/matplotlib 不完全一致；当前实现使用标准库和 Open-Meteo 路线，不要贸然加依赖。
- MCU 本轮 7SEG、开机帧、1Hz 事件和 `USER1` 语义改动尚未用 Keil/真板编译烧录验证；当前本机只找到 MinGW `gcc`，未发现 `UV4`/`armclang`/`armcc`/`arm-none-eabi-gcc`。
- 当前 PC 端 `.venv` 可运行 PyQt5；已用 Windows 原生 Qt 平台截图检查主页、系统设置、闹钟与日程管理、调试与测试的白天/黑夜模式。Qt offscreen 平台可能不渲染部分文字，最终视觉判断优先使用 Windows 原生平台或真实窗口。
- 正式提交材料尚未准备：未发现简介 PDF/Word、演示视频/PPT、正式截图集、`mcu/obj/*.axf` 可烧写产物。用户当前说暂时不着急提交材料，先把程序修到位。
- `docs/next-step-ui-and-host-fixes.md` 是旧任务书，里面的问题可能已经部分修复，不能直接当作当前 bug 清单。

## 下一步计划

优先做程序收口，不急着做提交材料。

1. 每次改动前先看 `git status`、`git diff` 和最新 `pc_host/app.py`，确认 Gemini 或用户已有改动。当前开发只在 `真正的最新版` 目录进行。
2. 下一步优先做硬件验证：Keil 编译、烧录 S800，实测 `USER1` 短按触发 PC 对时、长按切 DAY/NIGHT、`USER2` 天气短显、`FORMAT RIGHT` 与事件同步。
3. 再做上位机小 UI/交互收口：日程按钮显隐、文案、日志高度、无串口模式日志语义；继续保持黑夜模式无白底、按钮统一蓝色。
4. 每轮改完至少运行 Python 编译检查和 host-only 检查；涉及 UI 时做离屏启动/截图检查；涉及 MCU 时做 Keil 或可替代语法/构建验证。
5. 用户明确同意上传 GitHub 时，先给出上传文件清单和操作计划；确认后再执行，并同步更新 `PROJECT_CONTEXT.md` 与 `CHANGELOG_AI.md`。

## 2026-06-09 最新修复状态

本轮目标是修复 PC 上位机 UI 可读性、下拉框/勾选框样式、数字孪生镜像布局，以及对时/自动测试偶发卡死问题。当前已完成如下状态：

- 当前项目目录仍为 `D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\真正的最新版`，Git 分支为 `gemini-ui-improvements`，远程仓库为 `https://github.com/Cyh29hao/qianrushidazuoye`。
- PC 主窗口采用左右 `QSplitter` 明确分栏；右侧数字孪生镜像位于右侧顶部，日志区在其下方扩展，几何烟测显示不会覆盖左侧主页、系统设置或自动测试页面。
- 日志区启用可扩展高度、按宽度换行和滚动条；自动测试输出框同样启用换行和滚动，避免长行被裁掉。
- 下拉框/日期时间编辑器/spinbox 箭头改用 `pc_host/assets/*.xpm` 文本图标；checkbox 勾选改用 XPM 白色勾，避免 Qt QSS 三角形在 Windows 缩放下显示成黑色小方块。
- NTP 对时增加 token 过滤和 8 秒超时；串口日期/时间写入增加 5 秒超时；天气刷新增加 14 秒超时。超时后恢复按钮、写日志，不再让 UI 或自动测试流程悬挂。
- 一键对时/刷新天气后的自动测试只在 NTP、串口写入和天气刷新全部空闲后启动；手动点击自动测试时，如果对时/天气仍在进行，会等待后续空闲，避免抢占同一个串口。
- 自动测试脚本 `pc_host/run_extension_checks.py` 增加 stale input 清理、每条串口命令 timeout、串口 hard timeout 和命令间隔，失败会记录 FAIL/排查提示，不再无限等待。
- PC 本地/离线 USER2 没有有效天气 token 时显示 `NO WX`；MCU 端 USER2 非编辑状态显示天气短显，天气为空时也显示 `NO WX`，编辑状态仍作为 SUB 减一键。
- MCU 收到 `SET DATE`/`SET TIME` 时会清理天气/消息临时显示，收到 `SET WEATHER` 时校验空天气、清除天气短显计时并刷新显示，降低对时/天气更新后数码管卡在临时状态的风险。
- v2.1 UI/release 收口：左侧主内容宽度优先、右侧数字孪生降低最小宽度并增加按键行距；主要表单统一行高/边距；主页面关闭横向滚动；主要下拉框文本居中且可点击；串口下拉框仍可手动输入；下拉箭头改为实色 XPM 下三角，避免黑色小方块。
- v2.1 打包产物已生成：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`，压缩包为 `build_release/SmartClockHost-v2.1.zip`。`build_release/` 默认不提交到 Git。

本轮已验证：

- Python 语法检查通过：`pc_host/app.py`、`pc_host/twin_widgets.py`、`pc_host/run_extension_checks.py`、`pc_host/protocol.py`、`pc_host/extension_services.py`、`pc_host/extension_store.py`。
- `pc_host/run_extension_checks.py --host-only` 通过。
- PyQt offscreen 烟测通过：启动主窗口、切换 DAY/NIGHT、展开下拉框、切换页面，控制台未出现 `Could not parse stylesheet`；但 offscreen 截图可能不渲染中文，最终视觉仍以 Windows 真实窗口为准。
- v2.1 打包版 exe 烟测通过：启动 5 秒未退出；烟测日志/运行状态已清理后重新压缩 release zip。
- `git diff --check` 无空白错误，仅有 Windows 行尾提示。

仍需后续人工/硬件验证：

- 因 `mcu/src/main.c` 已修改，需要 Keil5 打开 `mcu/clock.uvprojx` 重新编译、烧录 S800 实板。
- 实板重点测：RESET 后一键对时/天气、USER2 天气短显/无天气 `NO WX`、自动测试串口步骤、数码管对时后是否回到正常时间显示。
- 已有 v2.1 `.exe` 包，但提交材料或演示前仍建议在 Windows 真实窗口下手动双击验证一次白天/黑夜、串口下拉、系统设置和自动测试页面。

## 2026-06-09 RESET/重连同步稳定版

本轮继续在 `真正的最新版` 主版本上修复串口连接、RESET/断电重插、板端时间恢复、PC 数字孪生镜像同步和昼夜模式状态恢复。

- 串口连接成功后，PC 会立即按当前地点/时区计算当前时间并写入 S800。该流程不等待 NTP；NTP 不可用时也能用 PC 本机时间 + 城市时区作为 fallback。写入后延迟查询运行状态，避免 `SET` 的 `OK` 和 `GET` 队列串台。
- 收到 `S800 CLOCK READY` 后，PC 会清空旧查询队列，数字孪生优先等待/映射板端真实 `*EVT:DISP` 帧；后台先做一次快速写时，再延后启动原有 NTP + 天气刷新流程。
- 数字孪生数据源优先级已明确：串口连接且收到板端显示帧时，永远以板端 `*EVT:DISP`/`*EVT:LED` 为准；串口连接但暂未收到新帧时保留最后一帧/等待状态；未连接串口或本地模式时才允许 PC 本地模拟显示。
- `MODE` 状态只接受 `DAY`/`NIGHT`。`runtime_state.json` 加载时会清洗非法值；`*EVT:MODE` 和 `*GET:MODE` 返回 `OFF` 或其它无效值时只写日志，不更新 UI、不污染主题和数据看板。
- MCU 增加 EEPROM 时间备份：启动时尝试从 EEPROM 恢复最近保存时间；PC/板端写入日期或时间后立即保存；正常运行时约每 10 秒保存一次。没有 RTC 时无法估计断电时长，所以这是“尽量不回到默认 00:00:00”的 fallback，PC 连接后仍会自动对时覆盖。
- 日程“板端标签”不再按 8 字符静默截断。PC 端清洗为最多 32 个 ASCII 字符并通过 `*SET:MSG` 下发，MCU 端沿用现有滚动消息状态机显示完整标签，数字孪生通过真实显示帧同步。
- 问候语时间段改为：05:00-10:59 早上好，11:00-13:59 中午好，14:00-17:59 下午好，18:00-21:59 晚上好，22:00-04:59 夜深了/注意休息。
- 主窗口分栏重新取中间值：左侧主功能区默认更宽以容纳日期、日程和设置控件；右侧数字孪生保留足够宽高，第二行按键不应被日志区裁切。普通窗口和全屏仍需在真实 Windows 窗口复核。

关键文件：
- `pc_host/app.py`
- `pc_host/extension_store.py`
- `mcu/src/main.c`

已验证：
- `python -m py_compile pc_host/app.py pc_host/extension_store.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/extension_services.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过。
- PyQt offscreen 几何烟测通过：1280x720、1366x768、1500x900 下右侧第二行按键无裁切，左侧主要滚动页无横向滚动条。
- MCU 替代语法检查通过：`gcc -fsyntax-only -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c`。

仍需人工/硬件验证：
- 需要 Keil5 重新编译并烧录 `mcu/clock.uvprojx`，验证 EEPROM 初始化是否在目标板正常、RESET 后是否恢复最近时间、串口连接后是否自动写入现实时间。
- 需要真实串口测试：PC 已连接时 RESET、断电重插、NTP/天气刷新中 RESET、自动测试中 RESET/短暂断开。
- PC 端 v2.1 打包版已重新生成：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`，压缩包为 `build_release/SmartClockHost-v2.1.zip`。`build_release/` 仍按 `.gitignore` 不提交。
