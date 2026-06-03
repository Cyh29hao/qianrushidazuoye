# 智能联网时钟系统

这个仓库是 ARM 大作业基础版工程，当前目标是先完成：
- `§3` S800 板本地基础功能
- `§4.1` PC 上位机核心功能
- `§5` 串口协议
- `§7` 提交目录结构

## 目录结构

```text
大作业524031910102-陈云海/
├─ mcu/
│  ├─ Inc/
│  ├─ Driverlib/
│  ├─ RTE/
│  ├─ src/main.c
│  ├─ obj/
│  ├─ listings/
│  ├─ clock.uvprojx
│  └─ clock.uvoptx
├─ pc_host/
│  ├─ .venv/
│  ├─ app.py
│  ├─ bootstrap_qt.py
│  ├─ diagnose_qt_runtime.py
│  ├─ launch_pc_host.ps1
│  ├─ launch_pc_host.bat
│  ├─ main.ui
│  ├─ ui_main.py
│  ├─ protocol.py
│  ├─ twin_widgets.py
│  └─ requirements.txt
├─ docs/
│  ├─ deployment.md
│  ├─ tech-notes.md
│  └─ debug-log.md
└─ README.md
```

## MCU 说明

- 基于官方 `EXP3` 模板改造。
- 自编核心逻辑集中在 [main.c](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/mcu/src/main.c>)。
- 当前已实现的主线包括：
  - `1 ms SysTick` 时基
  - 数码管动态扫描
  - 8 键 + USER1/USER2 防抖
  - 日期/时间/闹钟
  - 本地编辑状态机
  - `FORMAT LEFT/RIGHT`
  - 串口 `SET / GET / PING`
  - `*EVT:DISP / *EVT:LED / *EVT:MODE / *EVT:KEY / *EVT:ALARM / *EVT:EDIT`

## PC 说明

- 技术栈：`Python 3.12 + PyQt5 + pyserial`
- GUI 入口：`pc_host/app.py`
- 推荐启动入口：`pc_host/launch_pc_host.ps1`
- Qt 运行时自检：`pc_host/diagnose_qt_runtime.py`

## 快速开始

### PC 上位机

详细步骤见 [deployment.md](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/docs/deployment.md>)。

最短路径：

```powershell
cd D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\大作业524031910102-陈云海\pc_host
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PyQt5 pyserial
python -m PyQt5.uic.pyuic -o ui_main.py main.ui
.\launch_pc_host.ps1
```

### MCU

- 打开 `mcu/clock.uvprojx`
- 目标芯片：`TM4C1294NCPDT`
- 自编代码位于 `mcu/src/main.c`

## 当前状态

- Python 侧已完成：
  - `PyQt5` 与 `pyserial` 安装
  - `ui_main.py` 生成
  - 模块导入检查
  - `pyuic5` 可执行检查
- MCU 侧已完成：
  - `main.c` 主逻辑落地
  - `gcc -fsyntax-only` 语法检查
- 尚未完成：
  - 真机烧录验证
  - 实串口联调
  - `§4.2` 扩展功能
  - `§4.3` 自主增加功能

## 文档

- 部署与排错： [deployment.md](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/docs/deployment.md>)
- 联调与验收： [test-guide.md](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/docs/test-guide.md>)
- 答辩技术点： [tech-notes.md](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/docs/tech-notes.md>)
- 调试记录： [debug-log.md](</D:/桌面/大二下/大二下 嵌入式系统与接口技术/ARM/大作业524031910102-陈云海/docs/debug-log.md>)
