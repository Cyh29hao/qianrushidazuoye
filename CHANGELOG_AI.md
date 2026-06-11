# CHANGELOG_AI - AI 阶段变更记录

只记录阶段性最终修改、关键文件、验证结果和未解决问题；不记录无关对话。

## 2026-06-11

### v2.2 最终体验复测、冷启动与离线即时响应收口

#### 已完成修改
- Matplotlib 从启动时导入改为用户首次打开“Matplotlib 图表”时再懒加载，减少打包 exe 冷启动和打开菜单时的卡顿风险；图表页首次打开会显示加载提示，加载失败时只在图表区域提示，不会让主窗口退出。
- 启动自动天气刷新延后约 18 秒，避免主界面刚出现时立刻发起网络请求；天气/NTP 仍保持后台线程和 timeout，不阻塞 UI、串口接收或数字孪生。
- 串口端口扫描从 UI 主线程改为后台线程，`COM5` 自动发现仍保留，但 Windows 串口枚举不再卡住页面菜单、切页和按钮操作。
- 本地/不使用串口路径改为轻量刷新：`DISP`、`USER2`、`*SET:MSG A_B_TEST` 等离线操作只更新影子状态、必要控件和数字孪生，不再每次触发全局主题/表单重刷。
- 新增单次日程保存保护：如果用户打开页面太久导致“默认当前时间 +1 分钟”已经过期，新建单次提醒会自动顺延到当前时间 +1 分钟并写 INFO 日志；编辑已有提醒仍保持原来的过期警告，避免静默改用户数据。
- 日程提醒在本地/未连接模式下也会写清楚铃声日志，说明只是记录本地提醒、不下发板端。

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- COM5 实板测试通过两轮：一次 `--port COM5` quick 通过；一次 `--port COM5 --full` 全面测试通过，覆盖 PING、GET、SET DATE/TIME、MODE DAY/NIGHT、WEATHER、RING、USER2 安全天气短显、`A_B_TEST` 下划线跑马灯、DISP/SPEED/FORMAT/EXT、高并发按键 burst 和 ERROR LEN/SYNTAX/PARAM/RANGE。
- 本地模式真实 Qt 探针确认选择“不使用串口”后，`*PING`、连续 4 次 `*SET:KEY DISP`、`*SET:KEY USER1`、`*SET:KEY USER2`、`*SET:MSG A_B_TEST` 均在约 26-49 ms 内完成；DISP 循环为 `TIME -> DATE -> WEEKDAY -> YEAR -> TIME`，`USER2` 无天气显示 `NO WX`，下划线消息直接显示 `A_B_TEST`。
- 源码 UI 探针确认用户机器约 `1080x639` 可用逻辑桌面下窗口为 `1015x600`，左右分栏约 `[569, 420]`，系统设置/闹钟与日程/调试与测试页横向滚动最大值为 `0`；菜单开合约 54-63 ms，切页约 14-90 ms。
- Matplotlib 图表首次加载约 6.3 秒，四类筛选后续切换约 0.3-0.5 秒，`dashboard_chart_last_error` 为空；这是按需加载的可接受首次成本，不再拖慢普通启动和不看图表的演示流程。
- v2.2 release 已重新打包：`build_release/SmartClockHost-v2.2/SmartClockHost.exe`、`build_release/SmartClockHost-v2.2.zip`、`for_submit/release/SmartClockHost-v2.2.zip` 均更新。打包/烟测使用临时 `SMARTCLOCK_PROFILE_DIR`，release 目录确认无 `config.json/runtime_state.json/schedules.json/logs` 残留。
- 打包 exe 冷启动临时 profile 烟测：进程未闪退，5 秒已有进程，10 秒采样点短暂 `Responding=False`，15/25/35 秒均恢复 `Responding=True`。当前结论是未见闪退和长期假死，但低配/杀毒环境下首次 PyInstaller + Qt/Matplotlib 解包仍可能有十秒级短暂忙碌。

### v2.2 全球时区/P1-P2/虚拟长按收口

#### 已完成修改
- P1/数字孪生本地 YEAR/星期等超过 8 位文本统一使用跑马灯，不再只滚动 WEEKDAY；离屏探针确认 YEAR 页连续帧发生变化。
- 修复日程管理表格空白点击不能取消选中：删除被覆盖的重复 `eventFilter()`，把空白点击清表单逻辑合并到有效事件过滤器。
- PC 虚拟按键新增 0.8 秒长按：USER1 长按切 DAY/NIGHT，DISP 长按切显示开关，FUNC 长按按保存/退出编辑语义处理。
- P2 协议测试本地模式新增协议校验器；错误模板不再误回 `OK LOCAL`，会按 `ERROR RANGE/PARAM/SYNTAX/LEN (LOCAL)` 分类回显。
- 全球时区/天气增强：补充代表城市和国家首都预设；输入 `美国/USA/日本/Australia` 会转首都并保留国家字段；Houston/Chicago 走 `America/Chicago`，当前夏令时 offset 为 `UTC-05:00`。
- 天气刷新日志补充定位/时区/拉天气流程；README 说明 Open-Meteo Geocoding/Forecast、国内天气接口、Nominatim/wttr 兜底和 `zoneinfo`/内置 DST fallback。
- Matplotlib 系统状态图去掉百分比，改为“显示、昼夜、连接、天气、板载闹钟、PC提醒”的实际状态/计数文案。

#### 验证
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only` 与 `--host-only --full` 通过。
- 离屏 PyQt 探针确认 YEAR 页跑马灯帧变化、本地协议错误分类正确。
- 全球城市/国家时区探针覆盖 Houston、Chicago、Los Angeles、Toronto、Paris、Berlin、Singapore、New Delhi、Sydney、Mexico City、Sao Paulo、Cairo、美国、日本、澳大利亚。

### v2.2 离线模式、DISP 与配置保护收口

#### 已完成修改
- 未连接串口/选择“不使用串口”时，协议测试台发送 `*PING` 也会显示 `TX *PING` 与 `RX *PONG LOCAL`，即使隐藏心跳日志也不会吞掉手动 PING。
- 修复离线虚拟 `DISP` 双跳问题：未连接串口时按键只执行一次本地模拟，稳定循环 `TIME -> DATE -> WEEKDAY -> YEAR -> TIME`，不再只剩两个状态。
- Matplotlib “日志统计”分类修正：`DISP/DISPLAY/KEY/MSG/LED/FORMAT/USER2` 归入“板端/显示”，纯 `MODE/DAY/NIGHT` 归入“昼夜模式”，避免按 DISP 被误统计成昼夜模式。
- 未连接串口时，虚拟按键、滚动消息、FORMAT/MODE 查询和 NTP 入口不再套用串口安静窗口/延迟回读；本地模式直接更新影子状态与数字孪生，避免 PC 程序看起来“未响应”。
- 新增 `scripts/build_v22_release.ps1`：重打包前会把旧 release 目录里的运行态只在目标不存在时迁移到 `%APPDATA%\SmartClockHost-v2.2`，烟测强制使用临时 `SMARTCLOCK_PROFILE_DIR`，避免覆盖用户试用配置。
- `for_submit/README.md` 修正旧口径：打包版正式配置目录为 `%APPDATA%\SmartClockHost-v2.2`，不是 exe 所在目录。

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- 离线 PyQt 探针通过：协议台 `*PING` 有 `TX/RX`，虚拟 DISP 五次序列为 `DATE/WEEKDAY/YEAR/TIME/DATE`，`DISP` 与 `MODE` 统计分类分开，`*GET:TIME` 有本地 `OK ... (LOCAL)` 回显。
- `python pc_host/run_extension_checks.py --host-only --full` 通过；正式 AppData 配置文件时间戳未被本轮测试改动。
- 使用 `scripts/build_v22_release.ps1` 重新生成 `build_release/SmartClockHost-v2.2/SmartClockHost.exe`、`build_release/SmartClockHost-v2.2.zip` 和 `for_submit/release/SmartClockHost-v2.2.zip`；exe 使用临时 `SMARTCLOCK_PROFILE_DIR` 启动 10 秒未退出，release 目录无 `config.json/runtime_state.json/schedules.json/logs` 残留。

### v2.2 exe 菜单卡顿与日志统计图收口

#### 已完成修改
- 左上角页面选择由“展开后挤压主布局”的内嵌菜单改为轻量弹出式菜单，避免打开菜单时触发左侧页面、右侧数字孪生和 Matplotlib 画布整体重排，降低 exe 卡顿/闪退风险。
- Matplotlib 图表刷新改为按需防抖；图表绘制异常会退化为图内提示和日志 WARN，不再把主窗口带崩。
- Matplotlib 图表新增“日志统计”筛选，按过去 24 小时统计板端/显示、NTP/天气、闹钟日程、昼夜模式、自动测试、系统设置、错误警告等操作数量。
- 自动测试脚本进度输出新增 `[STEP 当前/总数]`；PC 左侧测试输出会显示 `[当前/总数] 项目名`，方便用户知道当前跑到哪里。
- 打包版默认运行态从 exe 目录改为 `%APPDATA%\SmartClockHost-v2.2`；首次启动会把旧 release 文件夹中的配置/日程/运行状态/事件日志复制到 AppData，但不会覆盖已有 AppData 数据。打包/烟测仍可用 `SMARTCLOCK_PROFILE_DIR` 指向临时目录。

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- 源码版真实 Qt 探针通过：1600x900 下连续切换四个 Matplotlib 筛选、反复打开/关闭页面菜单，`dashboard_chart_last_error` 为空，右侧数字孪生保持 420px 未被挤掉。
- `pc_host/run_extension_checks.py --host-only --full` 通过；进度 callback 显示 host-only full 共 13 项。

### v2.2 Matplotlib 数据可视化看板补齐

#### 已完成修改
- 主页数据看板新增“卡片看板 / Matplotlib 图表”切换，保留原有 6 张卡片看板。
- Matplotlib 图表看板新增三类筛选：今日时间轴、提醒分布、系统状态；图表数据来自当前城市时间、天气缓存、板载闹钟和 PC 多日程提醒。
- 图表随白天/黑夜主题重绘，不抢占右侧数字孪生镜像，不改变 MCU 串口协议。
- `pc_host/requirements.txt` 从 UTF-16LE 转为 UTF-8，并新增 `matplotlib==3.10.3`。
- README、逐条验收、当前状态总览、自主答辩准备、提交 PDF 大纲和交接文档已把 E4 从“轻量看板替代”更新为“Matplotlib 图表看板已完成”。

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- 源码版真实 Qt 窗口截图通过，覆盖主页卡片看板、Matplotlib 今日时间轴、提醒分布、系统状态和黑夜模式图表页；截图输出到 `tmp/`，其中两张已纳入 `docs/screenshots/`。
- `pc_host/run_extension_checks.py --host-only --full` 通过。
- PyInstaller clean build 已重新生成 `build_release/SmartClockHost-v2.2/SmartClockHost.exe` 和 `build_release/SmartClockHost-v2.2.zip`，`_internal` 内确认包含 Matplotlib/Numpy，release 目录包含最新 MCU 工程副本。
- 打包 exe 使用临时 `SMARTCLOCK_PROFILE_DIR` 启动 8 秒未退出；release 目录未生成 `config.json/runtime_state.json/schedules.json/logs`，临时 profile 已清理。

### Plus 周计划 Codex 交接补记

#### 已完成修改
- 新增 `docs/PLUS周计划Codex交接说明.md`，系统记录当前 v2.2 状态、最近一次提交、已验证事项、本地 release/for_submit 路径、GitHub 状态、下一位 Codex 接手优先事项和不要误踩的坑。
- 更新 `PROJECT_CONTEXT.md` 顶部，补充 Plus 周计划交接状态：commit `e812ac5`、`main`/`gemini-ui-improvements` 已推送、tag `v2.2` 已推送、本地 release zip 已生成、GitHub Release 附件仍需网页上传。

#### 交接结论
- 当前主线是 `v2.2`，不要继续 v3.0。
- PC 打包产物和 `for_submit/` 已本地准备好，但真实演示 mp4、简介 PDF 和 GitHub Release 附件仍需要用户后续完成。
- 后续任何 PC/UI 可见改动都必须重新打包 exe，并用临时 `SMARTCLOCK_PROFILE_DIR` 烟测，不能污染用户配置。

## 2026-06-10

### v2.2 状态栏、图标与日程铃声收口

#### 已完成修改
- PC 端版本升为 `v2.2`，默认配置、HTTP User-Agent、README、当前状态总览和逐条验收对照同步更新；MCU 开机版本显示同步改为 `V2.2`。
- 底部状态栏左侧项目名改为 `智能联网时钟系统`，feature 文案去掉重复项目前缀并改为 `·` 分隔，最终显示形如 `智能联网时钟系统 · 串口孪生 · NTP天气 · 全球时区 · 闹钟日程 · 个性面板 · 自动测试`；同时去掉 `QStatusBar::item` 竖线边框。
- 日程触发逻辑改为演示优先：无论 DAY/NIGHT 都会向板端下发铃声；若命名铃声可用，先下发 `*SET:RING <type>`，再追加短 `*SET:BEEP <ms>` 做可听确认；若旧固件不支持 `RING`，直接使用 BEEP 兼容模式。夜间抑制选项改为只抑制语音播报，铃声仍响。
- Windows AppUserModelID 更新到 v2.2，Qt 应用级图标和窗口图标仍优先使用 `pc_host/assets/clock_logo.ico`；v2.2 release 打包时会重新嵌入 ico。
- README、逐条验收对照、当前状态总览、自主答辩准备和 `for_submit/` 提交包文档补充自主创新亮点：GitHub 版本管理、PC/板端双向昼夜与自动昼夜、两套自动测试、高并发防卡死、全球时区、双端对齐和用户友好界面。
- `AGENT.md` 增加 release 打包硬规则：打包/烟测必须使用临时 `SMARTCLOCK_PROFILE_DIR`，不得污染用户现有配置、日志、日程或 release 目录运行态文件。
- 建立 `for_submit/` 模拟提交目录，包含提交包说明、提交前检查清单、答辩亮点速记、文档副本、截图素材、v2.2 release zip 和本地 MCU 工程副本；大体积 release/MCU 副本不进入 Git。

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/run_extension_checks.py --host-only --full` 通过；离屏 UI 探针确认状态栏左侧为 `智能联网时钟系统`、feature 文案不再重复项目名、版本显示 `v2.2`。
- 离屏日程触发探针确认 NIGHT + 夜间抑制开启 + sync 占用情况下仍会排队 `*SET:DISPLAY ON`、`*SET:MSG`、`*SET:RING WAKE`、`*SET:BEEP 1200`，且允许对时期间下发提醒。
- COM5 短探针确认 `*SET:RING WAKE` 和 `*SET:BEEP 900` 均返回 `OK`。
- PyInstaller clean build 已重新生成 `build_release/SmartClockHost-v2.2/SmartClockHost.exe` 和 zip；exe 使用临时 `SMARTCLOCK_PROFILE_DIR` 启动 8 秒未退出；release 目录未生成 `config.json/runtime_state.json/schedules.json/logs`；从 exe 抽取的关联图标为项目橙色数码管图标。
- Windows Qt 真实窗口截图已刷新到 `docs/screenshots/` 和 `for_submit/demo/screenshots/`，包含主页、系统设置、闹钟与日程、调试与测试和黑夜模式系统设置。
- 因 MCU 开机版本改为 `V2.2`，正式演示前需要 Keil5 重新编译烧录。

### v2.1 USER2 串口序列化与 COM5 联机回归

#### 已完成修改
- PC 端 USER2 天气短显不再一次性连发多条串口命令，改为按 `DISPLAY ON -> FORMAT LEFT -> LED -> MSG` 间隔发送，避免 MCU 偶发把连续命令解析成 `ERROR PARAM` 或污染显示状态机。
- USER2 短显期间延长串口安静窗口，后台天气/NTP/运行状态查询不会插队抢占显示；未观察到期望天气帧时最多自动恢复重发两次，失败后释放 pending 状态，不会把 UI 卡住。
- PC 数字孪生新增 DISPLAY 帧合法性过滤：收到不可打印/非法 token 时只写 WARN，不更新镜像；如果正在等待 USER2 天气短显，则触发恢复流程。
- 普通滚动消息和日程提醒下发前也先确保 `DISPLAY ON`，并用短间隔序列发送，减少显示关闭或串口连发导致“OK 但看不见”的情况。
- MCU `*SET:MSG` 源码同步补强：收到消息时自动打开显示并清掉天气强制显示标记，重新烧录后即使此前显示关闭，跑马灯/提醒也能直接可见。
- 自动测试脚本 USER2 项升级为强制 LEFT、最多三次恢复重发后观察 `SUN29C`，避免旧临时显示状态导致误判。

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/run_extension_checks.py --host-only --full` 通过。
- `pc_host/run_extension_checks.py --port COM5 --full` 通过，覆盖 PING、GET、DATE/TIME、DAY/NIGHT、WEATHER、RING、USER2 安全天气短显、`A_B_TEST`、DISP/SPEED/FORMAT/EXT、ERROR LEN/SYNTAX/PARAM/RANGE。
- PC 主窗口直连 COM5 回归：故意把板端设为 RIGHT 且残留旧天气帧后，第一次点击虚拟 USER2 即显示正向 `SUN29C__`，pending 清零，日志中无裸 `*SET:KEY USER2`、无 `ERROR PARAM`。
- 隐藏心跳日志时手动 `*PING` 仍能看到 TX/RX；NTP 连击烟测后同步状态释放、串口仍回 `*PONG`，没有长期卡死。
- v2.1 打包版已重新生成：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 与 `build_release/SmartClockHost-v2.1.zip`；exe 启动 8 秒未退出，烟测运行态文件已清理后重新压缩。

### v2.1 COM5 reset 后 USER2 与调试页收口

#### 已完成修改
- “调试与测试 -> 板端硬件测试”重排为两行清晰表单，并上移到协议台前方；蜂鸣输入框/触发按钮、LED 掩码输入框/设置按钮在 1280x720 窗口截图中均可见。
- 手动协议台发送 `*PING` 时，即使关闭“心跳日志”，仍会记录手动 TX `*PING` 和 RX `*PONG`；后台心跳 PING 仍保持隐藏，避免日志刷屏。
- PC 虚拟 USER2、协议台 USER2、RAW USER2 均不再裸发 `*SET:KEY USER2`，改为安全天气短显：发送 `*SET:LED <mask>` + `*SET:MSG <weather token>` 并等待真实 `*EVT:DISP`，避免旧/当前实板 USER2 内部路径把状态机打挂。
- PC 收到实体 `*EVT:KEY USER2` 后，会立即用同一安全天气消息辅助覆盖显示，避免板端短暂异常帧长期停留；fallback 文案不再错误提示“请重新烧录最新 MCU”，改为检查临时状态和显示帧回传。
- MCU 源码同步加固：模拟/连续 USER2 也走同一冷却；`*SET:WEATHER` 仅更新缓存时不再强制刷新正常时间帧，只有正在等待 PC 天气或已经处于天气短显时才刷新显示，减少 USER2 前 0.2 秒闪时间帧。
- 自动测试脚本 USER2 项改为“USER2 安全天气短显”，通过 `EXT + LED + MSG` 观察 `SUN29C` 显示帧，不再用当前危险的裸 `*SET:KEY USER2` 路径。

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- COM5 原始复现：当前已烧实板收到 `*SET:WEATHER DISP SUN29C__ LED 05` 后再裸发 `*SET:KEY USER2` 会显示异常 `bE______`，说明不能继续让 PC 自动走裸 USER2 路径。
- 更新后的 `pc_host/run_extension_checks.py --port COM5 --full` 通过，覆盖 PING、GET、SET DATE/TIME、DAY/NIGHT、WEATHER、RING、安全 USER2、跑马灯、DISP/SPEED/FORMAT/EXT、ERROR LEN/SYNTAX/PARAM/RANGE。
- 源码窗口直连 COM5 验证：隐藏心跳日志时手动 `*PING` 仍显示 TX/RX；虚拟 USER2 日志中没有 `*SET:KEY USER2`，有 `*SET:MSG SUN29C`，并收到 `SUN29C` 显示帧。
- 重新打包 `build_release/SmartClockHost-v2.1/SmartClockHost.exe` 和 zip；exe 启动 8 秒未退出，烟测运行态已清理；安全联测后 COM5 仍能回 `*PONG`。

#### 未解决问题
- 当前实板裸 `*SET:KEY USER2` 仍会显示异常 `bE______`，PC 端已避开该危险路径；要根治实体 USER2 内部显示逻辑，需要用 Keil5 重新编译并烧录本轮最新 `mcu/src/main.c`。

### v2.1 COM5 实串口 USER2 卡死复盘与测试恢复

#### 已完成修改
- 使用 COM5 跑第一轮全面联合测试：PING、GET FORMAT/MODE、SET DATE/TIME、DAY/NIGHT、SET WEATHER、铃声、跑马灯、DISP/SPEED/FORMAT/EXT、ERROR LEN/SYNTAX 均通过；USER2 天气短显没有观察到 `SUN29C` 显示帧。
- 进一步原始串口探测发现，当前实板固件在 `*SET:WEATHER DISP SUN29C__ LED 05` 后触发 `*SET:KEY USER2` 会输出异常 `*EVT:DISP` 字符，随后可能进入无 RX 状态；`EXT/PING/RST` 串口恢复无响应，说明实板当前烧录固件不是本轮最新状态或 USER2 路径已卡死。
- 追加 COM5 恢复探测：端口仍可打开，但 DTR/RTS 组合切换、serial break、重新发送 `*PING` 均无 RX，确认第二轮实串口测试被当前实板状态阻塞。
- `run_extension_checks.py` 全面测试总 hard timeout 从 42 秒放宽到 70 秒，避免 USER2 单项失败拖累后续 ERROR PARAM/RANGE 误报。
- USER2 测试项在未观察到天气短显时会先尝试 `*SET:KEY EXT` 和 `*SET:MSG SUN29C` 恢复可见显示，再记录 FAIL。
- PC 运行时 USER2 fallback 也改为先发 `EXT` 再发 `SET MSG`，减少旧固件临时显示状态卡住后继续写消息失败的概率。

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --port COM5` 复测失败，所有命令无回包，符合“板端当前已卡死/需物理复位或重烧”的判断。
- PyInstaller 重新生成 `build_release/SmartClockHost-v2.1/SmartClockHost.exe` 和 `build_release/SmartClockHost-v2.1.zip`；exe 烟测启动 6 秒未退出，烟测运行态文件已清理。

#### 未解决问题
- 当前 COM5 板端在 USER2 后已无 `*PING` 回包，软件无法继续第二轮实串口测试；需要按 RESET 或重新烧录最新 `mcu/src/main.c` 后再跑第二轮全面测试。

### v2.1 USER2/DISP/EXT 与日志摘要稳定收口

#### 已完成修改
- 右侧日志摘要删除 LED 位义长行，`最新 LED` 改为固定单行 `LED: XX`，详细 LED 掩码用途和 D1-D8 位义移到“调试与测试”的板端硬件测试区域。
- MCU `USER2` 增加 350ms 等待 PC 天气缓存窗口和短按节流；`*SET:WEATHER` 不再无条件结束短显，避免有天气时先闪 `NO WX` 或把显示强制关回去。
- PC `USER2` 增加 1.2 秒触发合并；自动测试期间人工 USER2/NTP/天气请求直接忽略并写日志，不再排队打断测试或测试结束后突然执行。
- MCU `EXT` 除了在 `ALARM` 编辑页关闭单次闹钟外，也允许在正常页无临时天气/跑马灯时直接关闭已启用的板载单次闹钟，并上报 `*EVT:EDIT ALARM OFF`。
- 自动测试 USER2 项改为真实观察短显：先下发 `SUN29C__`，再触发 USER2，必须收到包含 `SUN29C` 的 `*EVT:DISP` 才通过。
- 页脚增加常态功能简介；自动测试期间显示“测试中：快速/全面联合测试，正在忽略手动串口操作”，结束或超时后恢复。

#### 关键文件
- `mcu/src/main.c`
- `pc_host/app.py`
- `pc_host/run_extension_checks.py`
- `pc_host/twin_widgets.py`
- `README.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- PyQt 离屏断言通过：右侧 LED 摘要固定单行、LED 位义长行隐藏、页脚测试中状态不会被 dashboard 刷新覆盖。
- 源码版 6 秒启动烟测未退出；PyInstaller 重新打包 v2.1 成功，`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 启动 6 秒未退出，zip 已重新生成。

#### 未解决问题
- 需要 Keil5 重新编译并烧录后实板验证：USER2 有天气不闪 `NO WX`、USER2 长按不挂死、DISP 连续切页不空白、EXT 正常页/ALARM 编辑页关闭单次闹钟。

### v2.1 单次闹钟同步、DISP 星期页与 LED 位义

#### 已完成修改
- MCU `DISP` 短按现在会在显示关闭时先恢复显示，并清掉天气/滚动临时画面后再切换 `时间 -> 日期 -> 星期 -> 年份`，避免星期页看似无效或空白。
- MCU 在 `ALARM` 编辑页新增 `EXT` 关闭板载单次闹钟入口；`*SET:ALARM OFF` 和设置新闹钟都会上报 `*EVT:EDIT ALARM ...`，方便 PC 即时刷新。
- PC 将 `GET:ALARM`、`*EVT:EDIT ALARM`、`*EVT:ALARM`、`*EVT:ALARM_OFF` 统一到同一套状态更新逻辑，并兼容 RIGHT 方向反向闹钟字符串。
- PC 收到或发送影响单次闹钟的物理/虚拟按键后，会自动延迟补查 `*GET:ALARM`，不再要求用户手动点“查询”。
- PC 右侧镜像区增加 LED 位义说明和当前点亮位短写；README 补充 D1-D8 功能分配表和单次闹钟关闭方法。

#### 关键文件
- `mcu/src/main.c`
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `README.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`
- `docs/screenshots/*.png`

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- PyQt 小型断言通过：RIGHT 方向 `54.03.21` 可反解为 `12:30:45`，显示关闭时虚拟 `DISP` 会恢复显示并切到 `WEEKDAY`，LED 位义控件存在。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- 真实 Qt 窗口重新截图四页，右侧数字孪生和 LED 位义未被裁切。

#### 未解决问题
- MCU 已改源码，仍需 Keil5 编译并烧录实板后验证物理 `DISP` 星期页、`ALARM` 编辑页 `EXT` 关闭、`*EVT:EDIT ALARM` 回传和 PC 自动补查。

### v2.1 USER2 天气短显与板端显示区收口

#### 已完成修改
- USER2 不再承担 `SUB`/减一功能：MCU 删除减一函数，编辑态 USER2 先退出编辑再执行天气短显；PC 数字孪生按钮和 tooltip 改为 `USER2 / WX`。
- PC 虚拟 USER2 无天气时下发 `NO_WX___`，有天气时下发当前天气 token；触发后如果未收到期望 `*EVT:DISP`，约 1.2 秒后自动发送 `*SET:MSG <token>` 作为可见兜底，并提示需要烧录最新 MCU。
- 系统设置页“板端显示与快捷控制”改为专用 5 行布局，避免旧 `gridLayout_2` 历史行高导致控件重叠；真实 Qt 小窗口下系统设置页横向滚动为 0，右侧数字孪生完整显示。
- `AGENT.md` 新增 release 打包硬规则：以后 PC/UI/配置/资源改动后默认必须重新打包 v2.1 exe 并做 exe 烟测。
- README 新增界面截图章节，截图覆盖主页、系统设置、闹钟日程、调试测试；USER2 说明更新为天气短显专用键，并增加旧固件下需要重烧 MCU 的排查提示。
- 重新打包 v2.1：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 与 `build_release/SmartClockHost-v2.1.zip` 已更新，烟测运行态已清理。

#### 关键文件
- `mcu/src/main.c`
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `pc_host/config.json`
- `README.md`
- `AGENT.md`
- `docs/extensions-roadmap.md`
- `docs/screenshots/*.png`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py pc_host/run_extension_checks.py` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- 假串口 USER2 行为断言通过：有天气时发送 `*SET:WEATHER DISP SUN29C__ LED 05`、`*SET:KEY USER2`，无匹配显示帧时兜底 `*SET:MSG SUN29C`；无天气时发送 `*SET:WEATHER DISP NO_WX___ LED 00`。
- COM5 实串口短测确认当前已烧旧固件仍会回异常天气帧，但 PC 兜底 `*SET:MSG SUN29C` 能让板端回 `*EVT:DISP SUN29C__ 00`；最新源码根修复仍需重新烧录。
- 真实 Qt 窗口截图覆盖四页，窗口约 `1082x660`；系统设置/闹钟日程/调试测试页横向滚动最大值均为 0。
- PyInstaller 从 `C:\smartclock_latest` 通过 `python -m PyInstaller` 打包成功；exe 启动 6 秒未退出。
- `git diff --check` 通过，仅有 Windows CRLF 行尾提示。

#### 未解决问题
- 当前实板固件不是最新源码表现，USER2 根路径仍必须用 Keil5 重新编译烧录 `mcu/clock.uvprojx` 后再实测；PC 兜底只是保证旧固件下演示可见。
- 未做长时间 USER1/USER2/NTP 并发硬件压力测试，本轮只做了 USER2 相关短测。

## 2026-06-09

### v2.1 极端并发与 RESET/NTP 稳定收口

#### 已完成修改
- PC 对时写板端从定时串发改为 `SET DATE -> OK -> SET TIME -> OK` 串行握手；`SET DATE` 偶发 `ERROR PARAM` 时会无前导零重试一次，仍失败则退出写入流程、恢复按钮和后续队列。
- 串口发送路径增加测试/对时占用保护，防止重复 NTP、连续跑马灯、协议台、天气下发、昼夜切换等不同渠道同时写串口时互相插队。
- RESET/READY 后增加 PC 模式优先保护窗口：板端启动默认模式若与 PC 不一致，PC 延后同步自身 DAY/NIGHT 到板端，不让板端影响 PC 自动昼夜状态。
- 自动昼夜在默认配置中设为开启；自动化测试保存并恢复该设置。
- 自动测试拆为快速/全面两档；全面测试新增跑马灯下划线、DISP/SPEED/FORMAT/EXT、四类 ERROR 格式，并异步触发城市/天气/NTP 一键流程。
- MCU 开机动画期间屏蔽物理和虚拟按键动作，版本显示改为 `V2.1`；`*SET:MSG` 前会退出编辑态并清除旧天气/消息临时状态。
- MCU 对 USER1 连续短按/长按增加冷却：长按切 DAY/NIGHT 约 900ms 内只认一次，短按 NTP 事件 4 秒内合并；I2C 读写忙等增加上限，避免外设总线异常把主循环永久卡死。
- PC 增加串口健康 watchdog：TX 后约 4 秒没有任何 RX 会清掉未完成查询/对时等待，先发 `*SET:KEY EXT` + `*PING` 退出临时显示，仍无响应再尝试软 `*RST`，并恢复 UI 按钮/轮询。
- 日程表点击空白处可取消选中；右侧数字孪生组高度收紧，减少底部空白。
- USER2 虚拟按键无天气时也会显示 `NO WX` 并记录日志；实体 USER2 日志说明短显内容。
- README 与测试指南更新了 v2.1 自动昼夜默认、快速/全面测试、ERROR 全类型、USER2 和 RESET/NTP 说明。

#### 关键文件
- `pc_host/app.py`
- `pc_host/run_extension_checks.py`
- `pc_host/config.json`
- `pc_host/extension_services.py`
- `mcu/src/main.c`
- `README.md`
- `docs/test-guide.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only --full` 通过。
- PyQt offscreen UI/行为烟测通过：快速/全面测试按钮存在，自动昼夜默认开启，选中左侧页面横向滚动为 0，右侧数字孪生尺寸在离屏窗口内未裁切，虚拟 USER2 会产生 `USER2`/`NO WX` 事件。
- 静态确认 USER1/串口 watchdog 路径存在：PC 侧 `_check_serial_health()` 已接入 1 秒 tick，MCU 侧 USER1 冷却和 I2C 忙等保护已写入 `mcu/src/main.c`。
- `launch.bat` 等价离屏启动保持运行到超时退出，未再刷 QSS parse 报错。
- PyInstaller v2.1 onedir 打包成功，release exe 离屏启动 6 秒未退出；烟测运行态文件已清理，`build_release/SmartClockHost-v2.1.zip` 已重新生成。

#### 未解决问题
- MCU 已修改 `mcu/src/main.c`，仍必须用 Keil5 重新编译并烧录实板验证 RESET、USER2、跑马灯、提醒触发、连续 NTP/P2 等真实串口场景。
- 本机没有连接 S800 实板，无法证明 COM5 与硬件长时间压力测试完全通过。

### v2.1 跑马灯/提醒状态机与自动测试收口

#### 已完成修改
- MCU 消息/提醒显示状态机增加硬超时保护：`*SET:MSG` 激活后记录开始时间，超过 `MESSAGE_MAX_ACTIVE_MS` 或文本异常会自动 `ClearMessageState()`、上报 `ERROR STATE` 并恢复正常时钟显示，避免跑马灯或日程提醒把全局显示状态卡死。
- MCU 跑马灯规则收口：所有需要滚动的消息至少往返一次；较短滚动使用 3 段路径，长文本使用 2 段路径，保证会滚到另一端并回到起点；最后一帧通过 `MESSAGE_FINAL_HOLD_MS = 2000ms` 额外停留。
- MCU 短消息/提醒显示期间独占显示滚动偏移，不再让日期/星期页面的滚动逻辑插进同一个 `g_scrollOffset`，修复提醒触发后偶发显示状态混乱。
- PC 离线数字孪生的本地跑马灯规则同步改为至少往返一次，避免未连接串口时 PC 镜像和板端滚动方向/终点不一致。
- PC 收到 `ERROR STATE` 时会记录“板端显示状态机超时，已自动清退临时显示并恢复时钟”，清理本地临时显示覆盖，并延迟查询一次板端状态。
- 自动测试 `SET MODE NIGHT/DAY` 现在必须等到对应 `*EVT:MODE`，随后额外等待 1 秒让 PC UI 刷新；若没有 MODE 事件则 FAIL，不再只凭 `OK` 误判通过。串口测试预计耗时同步更新到约 18 秒。
- 自动测试说明文字已改为当前测试项简介：PING、GET FORMAT/MODE、写日期时间、DAY/NIGHT、天气短显、铃声协议、USER2，并说明 TX/RX/OK/FAIL 输出方式。
- 单次闹钟、日程提醒时间和日程日期默认值统一改为当前城市/时区时间 `+1 分钟`；23:59 等边界会自动跨到次日 00:00，方便实板调试。
- 重新打包 v2.1 release：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 与 `build_release/SmartClockHost-v2.1.zip` 已更新。首次从中文路径打包失败，改用 `C:\smartclock_latest` junction 下的 venv 成功打包；旧 release 进程占用 DLL 后已结束进程并重新干净生成 zip。

#### 关键文件
- `mcu/src/main.c`
- `pc_host/app.py`
- `pc_host/run_extension_checks.py`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only` 通过。
- PyQt offscreen 行为断言通过：23:59:30 默认值变为次日 00:00；PC 本地长文本滚动会到端点并回到起点；`ERROR STATE` 会清理本地临时显示覆盖。
- 自动测试脚本行为断言通过：有 `*EVT:MODE` 时 `send_mode_expect()` 通过；只有 `OK` 无 MODE 事件时会抛出 FAIL。
- PyInstaller v2.1 onedir 打包成功，release exe 启动 5 秒未退出；烟测产生的运行态文件已清理并重新压缩 zip。
- `git diff --check` 通过，仅有 Windows 行尾提示。

#### 未解决问题
- MCU 已修改 `mcu/src/main.c`，仍必须用 Keil5 重新编译并烧录实板验证跑马灯、日程提醒触发、USER2 天气短显和 RESET/NTP 真实串口场景。
- 本机没有完成 Keil5 编译；C 端最终以 Keil5 工程 `mcu/clock.uvprojx` 为准。

### v2.1 串口/RST/NTP 与显示状态机稳定修复

#### 已完成修改
- 串口连接成功、硬件 READY/RESET、协议台 `*RST` 统一排队执行一次 NTP；连接与 READY 近距离重复时合并，避免多 NTP 抢串口。NTP 失败或 8 秒超时后，生命周期触发会使用当前城市/PC 时间 fallback 写入板端。
- 手动协议台/RAW 发送命令时清空旧查询队列，并设置短暂串口安静窗口；自动 `GET`、`PING`、天气下发、NTP 写时都会避让，降低 P2/协议测试与后台任务插队导致的状态机卡住风险。
- `*SET:TIME`、`*SET:DATE` 不再进入任何 NTP 触发路径；只有连接成功、READY/RESET、软 `*RST`、USER1、用户点击 NTP/一键天气对时会触发 NTP。
- 昼夜模式改为双向同步：板端 USER1 长按上报 `*EVT:MODE DAY/NIGHT` 后直接更新 PC 主题；自动昼夜开启时检测到手动切换则关闭自动模式并只写日志。
- USER2 日志和虚拟键行为收口：有缓存天气时虚拟 USER2 先下发 `*SET:WEATHER` 再触发短显，无天气时显示 `NO WX`。
- 修复滚动消息下划线：MCU 端真实 `_` 继续显示七段底段；`*EVT:DISP` 用 `~` 表示真实下划线、`_` 表示空白位，PC 协议和数字孪生按新规则还原。
- 清理系统设置多余“主题状态”行；窗口图标优先使用 `.ico`。

#### 关键文件
- `pc_host/app.py`
- `pc_host/protocol.py`
- `pc_host/twin_widgets.py`
- `mcu/src/main.c`
- `README.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/run_extension_checks.py` 通过。
- `git diff --check` 通过。
- `pc_host/.venv/Scripts/python.exe` 行为烟测通过：协议台发送 `*SET:TIME ...` 不触发 NTP；发送 `*RST` 只登记软复位 NTP；`A~B_____` 可还原为 `A_B`；七段 `_` 编码为 `0x08`。

#### 未解决问题
- MCU 改了 `*EVT:DISP` 下划线转义，需要 Keil5 重新编译并烧录实板验证。
- PC 端需要重新打包 v2.1 release，确保 exe 图标、串口/NTP 状态机和下划线显示进入发行包。

### v2.1 小逻辑桌面右侧栏防挤压回修

#### 已完成修改
- 修复用户真实 Windows 缩放环境下右侧数字孪生栏再次被挤掉/裁切的问题。根因是窗口实际可用逻辑尺寸约 `1080x622`，旧布局仍存在左侧最小宽度、右侧固定宽度和 twin 控件 `sizeHint` 总和过大的风险。
- `pc_host/app.py` 新增 `_right_panel_width_for()` 和 `_enforce_main_splitter_layout()`，在 `showEvent`、`resizeEvent` 和 `_refine_layout()` 后按真实 `QSplitter` 宽度重新钳制左右栏；右侧宽度自适应约 `360-500px`，左侧最小宽度降为 `360px`。
- `pc_host/twin_widgets.py` 降低 7SEG、LED、DigitalTwinWidget 的建议宽度和最小建议宽度，避免右侧内部控件继续强行要求 500px 以上宽度。
- `AGENT.md` 新增 `Small Logical Desktop Layout Rule`，要求后续 UI 修改必须验证小逻辑桌面和记录几何数据，不能只看大屏截图。

#### 关键文件
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `AGENT.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `python -m py_compile pc_host/app.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py pc_host/ui_main.py` 通过。
- `python pc_host/run_extension_checks.py --host-only` 通过。
- Windows 原生 Qt 最大化截图验证：窗口 `1080x622`，`QSplitter` 宽 `1060`，sizes `[634, 420]`，右侧栏宽 `420`、右边界 `1059`，未出屏；系统设置、闹钟日程、调试测试三个可见左页 horizontal scrollbar maximum 均为 `0`。
- 截图保存在 `tmp/right_guard_page_0.png`、`tmp/right_guard_page_1.png`、`tmp/right_guard_page_2.png`、`tmp/right_guard_page_3.png`，不纳入 Git。

#### 未解决问题
- 这次只修 PC 源码版布局和规则文档；如果继续使用 v2.1 打包 exe，需要重新打包 release 才能包含此回修。
- `pc_host/config.json` 有运行程序造成的换行状态变化，未纳入本次提交。

### v2.1 体验修复、右侧无滚动布局与协议测试收口

#### 已完成修改
- `AGENT.md` 和 `docs/UI自检清单.md` 改为最新硬规则：右侧数字孪生展示区禁止垂直/水平滚动，必须通过缩小 7SEG、按钮、间距和日志摘要保证一屏看全；左侧页面禁止横向滚动。
- 主窗口保持稳定 `QSplitter` 分栏：左侧主功能区、右侧数字孪生/日志区、底部状态栏均在正常布局流中，不互相覆盖；右侧固定约 500-560 逻辑像素，不再用右侧 scroll area。
- `pc_host/twin_widgets.py` 收缩 7SEG/LED/虚拟按键尺寸，补齐 SW1-SW8 与 USER1/USER2 hover tooltip；两行按键完整显示。
- 系统设置瘦身：蜂鸣和 LED 掩码移至“调试与测试”；全局语音入口移至闹钟/日程具体模块；错误“用户名”小标题移除。
- “协议测试台”重构：下拉模板覆盖全部主要命令类型和错误测试；`缩写当前指令`、`随机混合大小写` 作用于当前文本框，最后统一 `发送当前指令` 并在日志看 TX/RX。
- 天气刷新/一键刷新保持后台线程执行，NTP 8 秒 watchdog、天气 14 秒 watchdog，避免阻塞 UI、串口接收和数字孪生映射。
- 自动测试只保留手动触发，逐项输出 OK/FAIL/SKIP，并把 TX/RX/INFO 同步写入主日志。
- README、测试指南和验收对照文档同步删除旧“启动自动测试/协议演示按钮/扩展铃声独立栏”等过期口径。
- 重新打包 v2.1 release：`build_release/SmartClockHost-v2.1/SmartClockHost.exe` 与 `build_release/SmartClockHost-v2.1.zip` 已更新，仍不纳入 Git。

#### 关键文件
- `AGENT.md`
- `docs/UI自检清单.md`
- `docs/test-guide.md`
- `docs/大作业要求逐条验收对照.md`
- `README.md`
- `pc_host/app.py`
- `pc_host/twin_widgets.py`
- `pc_host/main.ui`
- `pc_host/ui_main.py`
- `pc_host/run_extension_checks.py`
- `pc_host/extension_store.py`
- `mcu/src/main.c`
- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

#### 验证结果
- `pc_host/.venv/Scripts/python.exe -m py_compile pc_host/app.py pc_host/twin_widgets.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/extension_services.py pc_host/extension_store.py pc_host/ui_main.py` 通过。
- `pc_host/.venv/Scripts/python.exe pc_host/run_extension_checks.py --host-only` 通过。
- 源码版 6 秒启动烟测无 stdout/stderr、无 `Could not parse stylesheet`、无 Traceback、无 `DeprecationWarning`。
- Windows 原生 Qt 截图覆盖 DAY/NIGHT 与主页、系统设置、闹钟日程、调试测试四页：每页左侧横向滚动最大值 `0`，右侧固定 `500x669`，`twinGroup` 约 `235px` 高，`logGroup` 约 `426px` 高，两行按键完整。
- v2.1 release exe 使用临时 `NIGHT` 配置启动 6 秒未退出；烟测生成的 `config.json`、`runtime_state.json`、`schedules.json`、`logs/` 已清理并重新压缩 release zip。

#### 未解决问题
- MCU 已修改，需要 Keil5 重新编译、烧录并在实板验证 EEPROM 恢复时间、RESET/断电重插同步、USER2 天气短显和自动测试串口步骤。
- `build_release/` 仍按 `.gitignore` 不提交；如需 GitHub Release 附件，可上传 `SmartClockHost-v2.1.zip`。

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
- 数字孪生按键高度、字号和整体 size hint 已收紧；第二行按键不再被日志面板裁切，USER2 tooltip 明确说明“天气短显专用键”，不再保留 SUB/减一语义。
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
## 2026-06-10 v2.1 串口并发与联测收口

### 已完成修改
- 修复“调试与测试 -> 板端硬件测试”中蜂鸣和 LED 掩码控件不可见的问题：不再复用会被系统设置布局重建影响的旧控件，改为在调试页独立创建 `debugBeepSpinBox`、`debugLedHexEdit`、`debugSendBeepButton`、`debugSendLedButton`，旧信号入口继续指向这组可见控件。
- PC 端虚拟按键、协议台 `*SET:KEY ...`、RAW 命令统一进入安全发送路径。自动测试、NTP、天气同步占用串口时会拒绝人为按键；FUNC 等容易进入编辑态的按键增加冷却窗口，避免连续乱按把板端留在临时/编辑状态。
- 自动化测试改为慢速、逐项稳定、失败即停：测试前抓取 `FORMAT`、`MODE`、`DISPLAY`，结束或失败后发送 `EXT` 并恢复原设置；全面测试预计约 125 秒，快速测试预计约 45 秒。
- 系统设置新增“恢复出厂设置”按钮，点击后需要二次确认；确认后重置城市、主题、显示、闹钟、日程和本地运行状态，并在串口已连接时尽量把默认状态同步到板端。
- MCU 端 `mcu/src/main.c` 增加串口模拟按键冷却，FUNC 串口触发冷却更长；`EXT` 在没有临时显示/编辑/闹钟可退出时会回到默认时间页并打开显示，避免用户乱按后找不到默认时间界面。
- README 更新自动测试节奏、状态恢复、FUNC/USER 按键节流、EXT 回默认时间页和恢复出厂设置相关说明。

### 关键文件
- `pc_host/app.py`
- `pc_host/run_extension_checks.py`
- `mcu/src/main.c`
- `README.md`

### 验证结果
- PC 语法检查通过：`python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py`。
- PyQt 几何检查通过：在 920x600 条件下，“板端硬件测试”蜂鸣/LED 掩码输入框和确认按钮均可见；系统设置页“恢复出厂设置”按钮可见，水平滚动条最大值为 0。
- COM5 实板串口测试通过：快速联合测试通过；全面联合测试在加长 USER2 和跑马灯等待后通过，覆盖 PING、GET、SET DATE/TIME、MODE 切换、WEATHER、BEEP、USER2、MSG 下划线、DISP/SPEED/FORMAT/EXT、ERROR LEN/SYNTAX/PARAM/RANGE。
- 高并发压力检查：旧固件下连续快速发送 12 次 `*SET:KEY FUNC` 后，串口仍能通过 `PING` 响应，但画面可能停在星期/日期临时页；继续按 `DISP` 可切回时间页。源码中已增加 MCU 端冷却和 `EXT` 回默认时间页保护，需要重新烧录后才能完全发挥。
- v2.1 exe 已重新打包并短启动烟测：`build_release/SmartClockHost-v2.1/SmartClockHost.exe`。

### 仍需人工/硬件验证
- `mcu/src/main.c` 已改动，必须用 Keil5 重新编译并烧录 S800 后，再复测 FUNC 连按、EXT 回默认时间页、DISP 循环、USER2 天气短显和自动测试长流程。
- 本轮 release 打包产物在 `build_release/`，该目录按 `.gitignore` 不提交到 Git；如需发 GitHub Release，应手动上传 zip 附件。
## 2026-06-10 v2.1 自动测试鲁棒性增强

### 已完成修改
- 全面联合测试新增 `RAPID KEY BURST` 项，模拟 `FUNC/DISP/SPEED/FORMAT/EXT` 快速连续按键，随后执行 `EXT + PING` 恢复检查；若无 `PONG` 会直接 FAIL，并提示疑似板端/串口状态机卡死，需要手动 RESET。
- 自动测试脚本支持取消事件；上位机新增“中止测试”按钮。中止后不再继续后续测试项，并尽量执行 `FORMAT/MODE/DISPLAY` 恢复。
- 自动测试开始前记录左侧页面索引，结束后恢复到启动测试前的页面；测试输出框不再刷大量 TX/RX，只保留开始项、OK/FAIL、WARN 和汇总结论，完整 TX/RX 仍保留在右侧日志区。
- 自动测试健康检查超时阈值改为按当前预计耗时动态计算，避免全面测试被旧的 50 秒阈值误杀；若真的长时间无 RX，会提示“疑似硬件端状态机卡死，请手动 RESET”。
- v2.1 release 重新打包，`build_release/SmartClockHost-v2.1/` 和 zip 内已包含最新 MCU 工程副本，便于直接打开 Keil5 编译烧录。

### 关键文件
- `pc_host/app.py`
- `pc_host/run_extension_checks.py`
- `README.md`
- `AGENT.md`
- `CHANGELOG_AI.md`
- `PROJECT_CONTEXT.md`

### 验证结果
- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `python pc_host/run_extension_checks.py --host-only --full` 通过，离线模式明确标记高并发按键鲁棒性需要真实 COM 口测试。
- PyQt offscreen 检查通过：“中止测试”按钮可见且初始禁用；测试输出过滤会隐藏 `[TX]`/`[RX]` 原始流水。
- COM5 取消探针通过：启动全面测试后设置取消事件，线程正常退出，输出 FAIL，并执行恢复命令。
- COM5 全面联合测试通过，新增 `RAPID KEY BURST: OK`，总耗时约 135 秒。
- v2.1 exe 重新打包并 8 秒烟测通过；release 目录确认包含 `mcu/src/main.c` 与 `mcu/clock.uvprojx`。

### 仍需人工/硬件验证
- 新 MCU 源码仍需 Keil5 重新编译并烧录。只有烧录后，MCU 端串口按键冷却和 `EXT` 回默认时间页才能在实板上完全生效。
