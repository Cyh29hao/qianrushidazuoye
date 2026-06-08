# AGENTS - 本仓库开发规则

适用范围：本文件位于项目根目录，约束在本仓库内工作的 Codex、Gemini CLI 或其他 AI 编程代理。

## 接手顺序

1. 先读 `PROJECT_CONTEXT.md`。
2. 再读 `AGENT.md`，尤其是 UI 白天/黑夜主题、按钮、滚动条、状态栏和 OTA 禁止露出的规则。
3. 再读最新代码和关键文档，尤其是：
   - `mcu/src/main.c`
   - `pc_host/app.py`
   - `pc_host/protocol.py`
   - `pc_host/twin_widgets.py`
   - `pc_host/extension_services.py`
   - `pc_host/extension_store.py`
   - `docs/大作业要求/大作业题目-学生版_V1.2.pdf`
3. 旧交接文档、旧任务书和旧截图只作线索。若它们与当前代码冲突，以最新代码、正式题目和用户最新指令为准。

## 需求优先级

1. 用户当前明确指令。
2. 正式题目 `大作业题目-学生版_V1.2.pdf`。
3. 老师新增参考文件 `FAQ_常见问题解析_V1.0(1).pdf` 和 `MCU与PC端开发要求_V1.0(1).pdf`。
4. `PROJECT_CONTEXT.md`。
5. 其他项目文档。

新增 FAQ 和 MCU/PC 要求是参考资料，不要因为它们推翻现有 2.0 方案；但要用它们检查协议、按键语义、验收细节和环境说明。

## 工作树规则

- 开始前必须查看 `git status --short --branch` 和相关 `git diff`。
- 不要回退、覆盖或删除用户/Gemini/其他 AI 已做的改动，除非用户明确要求。
- 发现多个版本、备份或临时目录时，先判断主版本；不要随意删除。
- 当前主版本是项目根目录本身，即 `真正的最新版`；父目录中的 `大作业524031910102-陈云海-最新版` 是曾被误认的旧目录，只作参考，不要在那里继续开发或用它覆盖当前 `pc_host/app.py`。父目录中的 `0604`、`backups` 等也视为历史备份。
- `.venv/`、`__pycache__/`、日志、运行态缓存和编译中间产物不要提交或上传，除非用户明确要求。

## 编码与文件写入

- 包含中文的新增或修改文件必须使用 UTF-8。
- CSV 若面向 Windows Excel，优先 UTF-8 with BOM。
- 写完中文文件后要验证文件本身能以 UTF-8 读取，不要只相信终端显示。
- 手工编辑优先使用 `apply_patch`；不要用容易造成 Windows 中文 mojibake 的 shell 重定向写文件。
- 不要把完整聊天记录塞进文档。只记录最终状态、关键文件、已完成修改、未解决问题和下一步计划。

## 开发边界

- 先把程序修到位，暂时不急着做最终提交材料。
- 不要大改 UI 骨架；保留左侧菜单、右侧数字孪生、日志/异常区域的总体结构。
- 不要新增第三方依赖，除非用户明确要求或出现无法绕开的硬阻塞。
- MCU 自编核心逻辑集中在 `mcu/src/main.c`；`Driverlib/`、`Inc/`、`RTE/` 尽量保持原样。
- PC 端以 PyQt5 + pyserial + 标准库为主，维持无串口本地模式。
- 保护蓝色主按钮体系、数字孪生镜像、`不使用串口` 本地配置持久化语义。
- `config.json`、`schedules.json` 可能包含用户当前机器配置；提交前要确认是否按当前状态提交。

## 必查功能点

- S800 离线本地功能不能被 PC 扩展功能替代。
- PC 数字孪生要跟随 `*EVT:DISP <8字符> <dpHex>`、`*EVT:LED <hex2>`、`*EVT:MODE <STATE>` 等事件。
- `FORMAT RIGHT` 要同时影响数码管显示、`*GET` 应答和 `*EVT:DISP`，小数点跟随规则必须正确。
- `USER1` 和 `USER2` 语义要对齐正式题目和新增参考文件；若与当前代码冲突，先记录并谨慎修正。
- 无串口模式是正式 feature，不是错误态；日志不能一边说未连接，一边说已写入 S800。
- 网络失败时保留旧地点/旧天气，不要清空可用状态。

## 验证要求

按改动范围选择最小但有效的验证：

- Python 改动：运行 `python -m compileall pc_host` 或等效编译检查。
- 上位机逻辑改动：运行 `python pc_host/run_extension_checks.py` 的 host-only 路径或在 GUI 中运行离线检查。
- UI 改动：做离屏启动/截图检查，确认无明显重叠、白底漏网、文字遮挡。
- MCU 改动：优先用 Keil 编译；若当前环境不可用，至少做可替代的语法/结构检查，并明确验证缺口。
- 真机相关功能必须区分“已静态验证”和“已硬件验证”，不能夸大。

## GitHub 上传规则

只有在用户明确同意上传 GitHub 时才能执行 push。

上传前必须先给用户：

1. 准备上传的文件清单。
2. 排除文件清单。
3. 操作计划。
4. 当前未解决风险。

用户确认后再执行。每次上传 GitHub 的同时，必须根据本轮最终状态更新：

- `PROJECT_CONTEXT.md`
- `CHANGELOG_AI.md`

`CHANGELOG_AI.md` 只记录阶段性最终修改、关键文件、验证结果和未解决问题，不记录无关对话。

## 提交信息

若用户要求提交 commit，提交信息遵循 Lore 风格：第一行写为什么改；必要时用 `Constraint:`、`Rejected:`、`Confidence:`、`Scope-risk:`、`Tested:`、`Not-tested:` 等 git trailer 记录决策和验证。
