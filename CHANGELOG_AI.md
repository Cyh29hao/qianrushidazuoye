# CHANGELOG_AI - AI 阶段变更记录

只记录阶段性最终修改、关键文件、验证结果和未解决问题；不记录无关对话。

## 2026-06-09

### 主窗口 UI 布局稳定化与硬规则补充

#### 已完成修改
- `AGENT.md` 新增“UI 修改硬性规则：禁止再犯的布局问题”，明确禁止绝对定位覆盖、右侧数字孪生裁切、左侧页面被压窄、黑夜白底、按钮无色、下拉箭头黑方块、checkbox 无勾、小标题压线等问题。
- 新增 `docs/UI自检清单.md`，列出主页、系统设置、时间与同步、闹钟与日程、调试与测试、数字孪生、日志区、状态栏、DAY/NIGHT、普通窗口/全屏窗口等提交前检查标准。
- 主窗口顶层布局改为更稳定的左侧主功能区 + 右侧滚动容器 + 底部状态栏：左侧宽度限制在 520-600 逻辑像素，右侧数字孪生获得伸展优先级且不再被日志区覆盖。
- 右侧数字孪生镜像区改为正常布局流中的上方固定内容区，日志区位于下方；窗口高度不足时右侧区域纵向滚动，不再硬裁切第二行按键。
- 收缩数字孪生的 7SEG/LED size hint，右上角说明文字改为可收缩短标题，避免高 DPI 下撑出横向滚动或裁掉最右侧数码管。
- 状态栏按钮改为更紧凑的状态栏专用蓝色按钮样式，状态栏高度限制为 34px，减少底部拥挤。
- 全局 `QGroupBox` 标题背景改为分组框底色并下移，缓解小标题贴线/压线问题。
- 重新打包 v2.1 release：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 与 `build_release/SmartClockHost-v2.1.zip` 已更新，仍不纳入 Git。

#### 关键文件
- `AGENT.md`
- `docs/UI自检清单.md`
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过。
- Windows 原生 Qt 平台截图验证覆盖 DAY/NIGHT 与 4 个页面：在 1366x819 逻辑窗口下，左侧页面 `hmax=0`，右侧 `hmax=0`，数字孪生与日志间距 10px。
- 几何验证覆盖 1600x900 与 2048x1228：DAY/NIGHT、4 个页面均无左/右横向滚动，数字孪生和日志不重叠。
- 使用 `pc_host/.venv/Scripts/python.exe pc_host/app.py` 等价启动 5 秒，无 stderr 输出，无 `Could not parse stylesheet`。
- v2.1 release exe 启动 5 秒未退出；烟测运行态文件已清理并重新压缩 release zip。

#### 未解决问题
- 本轮未修改 MCU，不需要重新烧录。若演示使用 release exe，必须使用本轮重新打包后的 `build_release/SmartClockHost-v2.1/SmartClockHost.exe`。
- 仍建议在用户真实桌面双击 release exe 复核一次最大化和普通窗口，尤其确认高 DPI 下右侧纵向滚动条不会影响演示观感。

### RESET/重连同步稳定版

#### 已完成修改
- 串口连接成功后自动按当前城市/时区写入板端日期和时间；NTP 不可用时使用 PC 本机时间 + 城市时区 fallback，不再要求用户手动点一次对时。
- 收到 `S800 CLOCK READY` 后清空旧查询队列，先等待/映射板端真实显示帧，再后台执行快速写时、NTP 对时和天气刷新，避免 PC 本地开机镜像或网络任务覆盖真实板端显示。
- 数字孪生镜像加固数据源优先级：连接串口时本地模拟刷新和本地开机镜像不再写入孪生画面；真实 `*EVT:DISP`/`*EVT:LED` 永远优先。
- `MODE` 清洗为只允许 `DAY`/`NIGHT`：加载运行状态、收到 `*EVT:MODE`、处理 `*GET:MODE` 时都会忽略 `OFF` 等非法值，避免 RESET/插拔后 UI 显示 `OFF`。
- MCU 增加 EEPROM 时间备份：启动读取最近时间，写日期/写时间后立即保存，运行时每约 10 秒保存一次，作为无 PC 时 RESET 后不回到默认 00:00:00 的稳妥 fallback。
- 日程板端标签改为最多 32 个 ASCII 字符，通过 `*SET:MSG` 走 MCU 原有滚动消息状态机显示完整标签，不再静默截断前 8 位。
- 问候语改为 05-10 早上、11-13 中午、14-17 下午、18-21 晚上、22-04 夜深了/注意休息。
- 主窗口左右比例微调：左侧主功能区变宽以容纳日期、系统设置和日程控件；右侧数字孪生固定高度增加，避免第二行按键被日志区裁切。

#### 关键文件
- `pc_host/app.py`
- `pc_host/extension_store.py`
- `mcu/src/main.c`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`
- `AGENT.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/extension_store.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/extension_services.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过。
- PyQt offscreen 几何烟测通过：1280x720、1366x768、1500x900 下右侧第二行按键无裁切，左侧系统设置/日程/调试页无横向滚动条。
- `gcc -fsyntax-only -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- PyInstaller onedir 重新打包成功：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`；压缩包 `build_release/SmartClockHost-v2.1.zip` 已更新，exe 启动 5 秒未退出，release 目录运行态文件已清理。

#### 未解决问题
- MCU 改动需要 Keil5 重新编译、烧录并在实板验证 EEPROM 恢复时间、串口连接自动写时和 RESET 显示帧同步。
- `build_release/` 仍按 `.gitignore` 不提交；如需 GitHub Release 附件，可上传 `SmartClockHost-v2.1.zip`。

### v2.1 真实窗口布局回修

#### 已完成修改
- 根据用户真实窗口反馈，重新收紧主窗口 `QSplitter` 策略：左侧主内容默认约 460px，最大 560px；右侧数字孪生最小 620px 并获得伸展优先级，避免左侧超宽压住右侧。
- 右侧数字孪生固定在右上方，按键行高度和字体略收紧；日志区降低最小高度，只占用数字孪生下方剩余空间，避免第二行按键被裁切。
- 系统设置行改为更窄的标签/字段/按钮比例；移除 `gridLayout_2` 旧空列最小宽度，修复系统设置按钮只显示半截的问题。
- 网络对时状态标签和当前时间标签改为可换行、可收缩，避免长城市/时区文本把系统设置页 host 撑宽。
- 调试页“时间写入与 NTP 对时”压缩列宽，日期/时间编辑器允许收缩，写入/查询按钮改为窄按钮；自动测试页 host 不再宽过左侧 viewport。
- `AGENT.md` 新增布局硬规则：必须检查 1280x720、1366x768 和全屏；左侧 scroll host 不得宽过 viewport；右侧日志不得压住数字孪生两行按键。

#### 关键文件
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `.gitignore`
- `AGENT.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过；测试产生的 `config.json`/`schedules.json` 运行态回写已恢复，未纳入提交。
- PyQt offscreen 几何烟测通过：1280x720、1366x768、1500x900 下系统设置页 host 等于 viewport，系统设置按钮完整显示；调试页 host 等于 viewport；右侧数字孪生与日志上下分栏不覆盖。
- PyQt offscreen 截图烟测覆盖主页、系统设置、调试页、夜间模式；离屏环境中文可能不渲染，但布局无明显遮挡。
- PyInstaller onedir 重新打包成功：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`；压缩包 `build_release/SmartClockHost-v2.1.zip` 已更新，打包版 exe 启动 5 秒未退出，烟测运行态已清理后重新压缩。

#### 未解决问题
- 真实 Windows 字体/DPI 与 offscreen 仍可能有差异，明天需要在用户机器上用 `launch.bat` 双击复核 1366x768/全屏效果。
- 打包产物在 `build_release/` 下，按 `.gitignore` 不纳入 Git 跟踪；如需 GitHub Release，需要上传 zip 作为 release 附件。

### v2.1 UI 收口与发布准备

#### 已完成修改
- PC 端版本号更新为 `v2.1`，默认配置 `app_version` 同步更新为 `2.1`。
- 主窗口 `QSplitter` 分栏重新调权：左侧主内容优先保留 600px 以上宽度，右侧数字孪生降低最小宽度并增加按键行距，避免右侧面板挤压或遮挡主页、系统设置、自动测试页面。
- 主页改为无滚动条一屏布局，数据看板压缩为 3 列 x 2 行；1280x720 烟测下主页不再出现滚动条。
- “日志与异常”标题移入日志框内部，避免 `QGroupBox` 标题骑线、超出边框或形成“爆炸线”。
- 调试页时间模块改为 `时间写入与 NTP 对时`，新增手动写入/NTP 自动对时说明，并压缩 NTP 按钮与日期/时间写入行之间的垂直间距。
- 系统设置行布局改为统一标签/字段/按钮三段式，缩短按钮宽度并保留字段弹性伸展；网络对时、铃声、单次闹钟、日程、自动测试等同类页面统一边距、行高、标签宽度和控件间距。
- 关闭各主页面横向滚动，改为依赖弹性布局，避免出现“控件没有右边界”“需要横向拖动才看全”的展示问题。
- 主要下拉框改为只读可点击且文本居中；串口下拉框保留可手动输入 `COM5`。
- 下拉箭头资源改为实色背景 XPM，并加粗为实心下三角，避免透明背景在部分 Qt/Windows 平台显示为黑色小方块。
- 主页数据看板卡片文本改为居中排版，继续保持卡片式展示。

#### 关键文件
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `pc_host/extension_store.py`
- `pc_host/config.json`
- `pc_host/assets/combo_arrow_day.xpm`
- `pc_host/assets/combo_arrow_night.xpm`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `pc_host\.venv\Scripts\python.exe -m py_compile pc_host\app.py pc_host\twin_widgets.py pc_host\run_extension_checks.py pc_host\protocol.py pc_host\extension_services.py pc_host\extension_store.py` 通过。
- `pc_host\.venv\Scripts\python.exe pc_host\run_extension_checks.py --host-only` 通过。
- PyQt offscreen 烟测通过：1280x720 下系统设置页和自动测试页无横向滚动；分栏约 `655/591`；主要下拉框为居中、只读、可点击状态；白天/黑夜模式箭头均可见。
- PyInstaller onedir 打包成功：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`；压缩包 `build_release/SmartClockHost-v2.1.zip` 已生成，大小约 40 MB。
- 打包版 exe 烟测通过：启动 5 秒未退出，随后手动结束进程；烟测产生的运行状态/日志已从 release 目录清理并重新压缩。
- `git diff --check` 无空白错误，仅有 Windows 行尾提示。

#### 未解决问题
- Qt offscreen 截图仍可能不显示中文文字，最终展示效果需要在 Windows 真实窗口下复核。
- 打包产物在 `build_release/` 下，按 `.gitignore` 不纳入 Git 跟踪；如需发 GitHub Release，可上传 zip 作为附件。
- MCU 本轮未新增改动，但此前 MCU 改动仍需 Keil5 重新编译、烧录实板验证。

### 本轮 UI 布局与对时稳定性修复

#### 已完成修改
- PC 主窗口保持左右 `QSplitter` 明确分栏；右侧数字孪生镜像固定在右侧顶部，日志区在其下方扩展，不再覆盖左侧主页、系统设置或自动测试页面。
- 日志区改为可扩展高度，启用按控件宽度换行和滚动条，避免被数字孪生或底部状态栏遮挡。
- 数字孪生按键高度、字号和整体 size hint 已收紧；第二行按键不再被日志面板裁切，USER2 tooltip 明确说明“非编辑状态显示天气短显，编辑状态作为 SUB 减一键”。
- 下拉框、日期/时间编辑器、spinbox 箭头改用 Qt 稳定支持的 XPM 图标；checkbox 勾选状态改用 XPM 白色勾，避免黑色小方块和“只有框没有勾”的问题。
- NTP 对时增加 token 过滤与 8 秒 watchdog；时间写入增加 5 秒 watchdog；天气刷新增加 14 秒 watchdog，超时后恢复 UI 并写日志。
- 一键对时/刷新天气后的自动测试只会在 NTP、串口写入和天气刷新全部空闲后启动，避免自动测试抢占同一个串口导致偶发失败。
- 自动测试串口脚本增加 stale input 清理、每条命令 timeout、串口总 hard timeout 和命令间隔，失败时返回 FAIL 而不是无限等待。
- PC 离线/本地 USER2 如果没有有效天气 token，会显示 `NO WX` 而不是空白短显。
- MCU `USER2` 非编辑状态显示天气短显；无有效天气时显示 `NO WX`。MCU 收到 `SET DATE`/`SET TIME` 时会清理天气/消息临时显示，收到 `SET WEATHER` 时校验空天气并刷新显示，减少对时后数码管卡在临时状态的风险。

#### 关键文件
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `pc_host/run_extension_checks.py`
- `pc_host/assets/*.xpm`
- `mcu/src/main.c`

#### 验证结果
- `pc_host\.venv\Scripts\python.exe -m py_compile pc_host\app.py pc_host\twin_widgets.py pc_host\run_extension_checks.py pc_host\protocol.py pc_host\extension_services.py pc_host\extension_store.py` 通过。
- `pc_host\.venv\Scripts\python.exe pc_host\run_extension_checks.py --host-only` 通过。
- PyQt offscreen 烟测通过：创建主窗口、切换 DAY/NIGHT、展开下拉框、切换页面后未出现 `Could not parse stylesheet`；几何检查显示右侧数字孪生和日志区未覆盖左侧。
- `git diff --check` 无空白错误，仅有 Windows 行尾提示。

#### 未解决问题
- MCU 已改 `mcu/src/main.c`，需要用 Keil5 打开 `mcu/clock.uvprojx` 重新编译、烧录实板验证。
- 本机离屏截图受 Qt offscreen 字体渲染限制，中文文字可能不显示；最终 UI 可读性仍建议在 Windows 真实窗口下复核。
- PC 若提交或展示 `.exe`，需要重新打包以包含本轮 UI 和稳定性修复。

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
## 2026-06-09 v2.1 release NIGHT 首启白底修复

### 已完成修改
- 修复 v2.1 打包版首次以 `NIGHT` 模式启动时，右侧数字孪生/日志区域短暂或持续显示系统默认白底的问题。
- `pc_host/app.py` 现在在动态布局创建完成后、窗口首次显示前后都会执行一次最终主题刷新；右侧/左侧主容器被赋予明确对象名并纳入全局主题 QSS。
- 同步给 `QApplication`、主窗口、central widget、左右主面板、`twinGroup`、`logGroup`、日志框等设置主题 palette，避免 release exe 第一帧还没完成 QSS polish 时露出白底。
- 重新生成 v2.1 打包产物：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 和 `build_release/SmartClockHost-v2.1.zip`。`build_release/` 仍按 `.gitignore` 不纳入 Git 跟踪。

### 关键文件
- `pc_host/app.py`
- `AGENT.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

### 验证结果
- `python -m py_compile pc_host\app.py pc_host\extension_store.py pc_host\protocol.py pc_host\twin_widgets.py pc_host\run_extension_checks.py pc_host\extension_services.py` 通过。
- `python pc_host\run_extension_checks.py --host-only` 通过。
- PyQt offscreen 强制 `NIGHT` 首启 palette 检查通过：`central`/`leftPanel`/`rightPanel` 为 `#18212b`，`twinGroup`/`logGroup` 为 `#223041`，`statusbar` 为 `#151e27`。
- v2.1 release exe 使用临时 `NIGHT` 运行状态启动 5 秒未退出；烟测生成的运行状态和日志已清理。

### 未解决问题
- 仍建议在真实 Windows 桌面双击 `build_release/SmartClockHost-v2.1/SmartClockHost.exe`，确认首屏 NIGHT 模式不再出现白底；offscreen 可验证 palette，但真实 DPI/显卡绘制仍以本机肉眼复核为准。
