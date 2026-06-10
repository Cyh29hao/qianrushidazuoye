# PLUS 周计划 Codex 交接说明

更新时间：2026-06-11 00:50 左右  
当前主目录：`D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\真正的最新版`  
当前主分支：`gemini-ui-improvements`，已同步推到 GitHub `main`  
当前版本：`v2.2`

## 1. 当前整体状态

这个项目已经从基础数字钟收口到可演示的 v2.2：S800/TM4C1294 板端 + PyQt5 PC 上位机，支持串口同步、数字孪生、NTP 对时、天气短显、全球时区、自动昼夜、板载单次闹钟、PC 多日程提醒、滚动消息、蜂鸣/铃声、LED 掩码、快速/全面两套自动测试和高并发防卡死保护。

用户目前基本满意，近期最后确认的三个问题是：

- PC 日程提醒铃声已经能听到；
- 底部状态栏改为 `智能联网时钟系统 · 串口孪生 · NTP天气 · 全球时区 · 闹钟日程 · 个性面板 · 自动测试` 风格；
- exe 图标已通过 PyInstaller 嵌入和关联图标抽取验证，如果 Windows 资源管理器仍显示默认图标，优先怀疑图标缓存。

## 2. 最近一次已完成的事情

最近一次提交：`e812ac5 Prepare the v2.2 submission handoff`

已完成：

- 修改 `pc_host/app.py`
  - 增加 `SMARTCLOCK_PROFILE_DIR`：打包/烟测时可把运行态写到临时目录，避免污染已有 `config.json`、日志、日程和 release 目录配置。
  - footer feature 文案改为 `·` 分隔。
  - 去掉 `QStatusBar::item` 左边框，避免底部又出现类似 `|` 的竖线。
- 更新 `AGENT.md`
  - 明确每版 release 打包/烟测必须使用临时 `SMARTCLOCK_PROFILE_DIR`，不能改用户已有配置。
- 更新 `README.md`
  - 补充 v2.2 自主创新亮点。
  - 补充 release 烟测不要污染配置的说明。
- 更新 `CHANGELOG_AI.md`、`PROJECT_CONTEXT.md`
  - 记录 v2.2 收口状态、验证结果和 release 打包规则。
- 更新验收/答辩文档
  - `docs/大作业要求逐条验收对照.md`
  - `docs/当前项目状态总览.md`
  - `docs/自主答辩准备初稿.md`
  - `docs/用户体验自测与改进建议.md`
- 新建并整理 `for_submit/`
  - 轻量说明和文档副本已纳入 Git。
  - `for_submit/release/` 和 `for_submit/mcu/` 是本地大文件/工程副本，已 `.gitignore`，不进 Git。
  - `for_submit/demo/screenshots/` 放了当前 v2.2 页面截图素材。
- 重新打包 v2.2 exe/zip
  - `build_release/SmartClockHost-v2.2/SmartClockHost.exe`
  - `build_release/SmartClockHost-v2.2.zip`
  - `for_submit/release/SmartClockHost-v2.2.zip`
- GitHub
  - `gemini-ui-improvements` 和 `main` 都已推到 `e812ac5`。
  - `v2.2` tag 已推送。
  - GitHub Release 页面还没有创建成功，因为本机没有 `gh`，也没有可用 GitHub API token；需要用户在网页上基于 tag `v2.2` 新建 release 并上传 zip。

## 3. 已验证事项

最近验证过：

- `python -m py_compile pc_host/app.py pc_host/run_extension_checks.py pc_host/protocol.py pc_host/twin_widgets.py pc_host/extension_services.py pc_host/extension_store.py` 通过。
- `pc_host/run_extension_checks.py --host-only --full` 通过。
- `gcc -fsyntax-only -std=c99 -DPART_TM4C1294NCPDT -DTARGET_IS_TM4C129_RA0 -I mcu/Inc -I mcu/Driverlib mcu/src/main.c` 通过。
- PyInstaller clean build 成功，日志显示 `Copying icon to EXE`。
- 打包 exe 使用临时 `SMARTCLOCK_PROFILE_DIR` 启动 8 秒未退出。
- release 目录确认没有 `config.json`、`runtime_state.json`、`schedules.json`、`logs/`。
- 从 exe 抽取的关联图标是项目橙色数码管图标。
- Windows Qt 真实窗口截图已刷新到 `docs/screenshots/` 和 `for_submit/demo/screenshots/`。

## 4. 重要本地路径

- 最新 exe：`build_release/SmartClockHost-v2.2/SmartClockHost.exe`
- 最新 zip：`build_release/SmartClockHost-v2.2.zip`
- 提交模拟目录：`for_submit/`
- GitHub 仓库：`https://github.com/Cyh29hao/qianrushidazuoye`
- 当前 tag：`v2.2`

## 5. 下一位 Codex 接手时优先事项

1. 不要再碰 3.0 本地激进改版，用户明确说 3.0 不要管了。
2. 优先保持 v2.2 稳定，不要大重构。
3. 用户默认检查 exe，不默认看源码；任何 PC/UI/文档可见变化后都要重新打包 v2.2 exe/zip。
4. 打包/烟测必须设置临时 `SMARTCLOCK_PROFILE_DIR`，不要污染用户现有配置。
5. 如果继续做 release，需要在 GitHub 网页创建 `v2.2` Release 并上传 `build_release/SmartClockHost-v2.2.zip`。
6. 正式交付前，用户还需要用 Keil5 重新编译并烧录 MCU，确认开机 `V2.2`。
7. 正式交付前，用户需要录制真实 S800 + PC 的 ≤5 分钟演示 mp4，并制作 4-8 页简介 PDF。
8. 若再改 UI，必须遵守 `AGENT.md` 的 UI 硬规则和 release 打包硬规则。

## 6. 不要误踩的坑

- `build_release/`、`for_submit/release/`、`for_submit/mcu/`、`mcu/obj/`、`pc_host/logs/`、`pc_host/runtime_state.json` 等不要提交。
- `pc_host/config.json` 是用户运行态，除非明确要改默认配置，否则不要随便覆盖。
- 右侧数字孪生和左侧主内容布局之前反复被改坏，任何 UI 修改都必须真实打开窗口检查。
- USER2、DISP、EXT、NTP、天气、跑马灯和高并发按键是用户反复重点测试过的敏感区；改这些必须做实机或至少串口脚本验证。
- GitHub Release 附件没有自动上传成功，不要误报“Release 已上传”。目前只完成了代码推送和 tag 推送。

## 7. 用户下一步应该做

1. 打开 GitHub 仓库 Releases，选择 tag `v2.2` 新建 release。
2. 上传本地 `build_release/SmartClockHost-v2.2.zip`。
3. Keil5 重新 Build + 烧录 MCU。
4. 双击 exe，连接 COM5，跑快速/全面联合测试。
5. 按 `for_submit/submission/演示视频_5分钟脚本初稿.md` 录视频。
6. 按 `for_submit/submission/简介PDF_4-8页初稿大纲.md` 做最终 PDF。
7. 最终按 `for_submit/README_提交包说明.md` 和 `for_submit/提交前检查清单.md` 打包提交。
