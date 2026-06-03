# PC 端完整部署指南

这份指南只负责 `pc_host/` 的 GUI 上位机部署、运行、验收与排错。

## 1. 你应该用哪个入口

优先使用：

```powershell
.\launch_pc_host.ps1
```

不要把第一次验收入口写成裸命令：

```powershell
python app.py
```

原因：
- `launch_pc_host.ps1` 会强制使用项目自己的 `.venv` 解释器。
- 当前工程已经在代码入口里补了 Qt 插件路径，但脚本入口更不容易走错环境。

如果 PowerShell 禁止执行脚本，先运行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 2. 首次部署

进入目录：

```powershell
cd D:\桌面\大二下\大二下 嵌入式系统与接口技术\ARM\大作业524031910102-陈云海\pc_host
```

创建虚拟环境：

```powershell
python -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PyQt5 pyserial
```

生成 UI Python 文件：

```powershell
.\.venv\Scripts\pyuic5.exe --version
python -m PyQt5.uic.pyuic -o ui_main.py main.ui
```

生成依赖清单：

```powershell
pip freeze > requirements.txt
```

## 3. 启动方式

推荐启动：

```powershell
.\launch_pc_host.ps1
```

批处理入口也可用：

```bat
launch_pc_host.bat
```

## 4. 验收前自检

### 4.1 Python 与包

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "from PyQt5 import QtCore; import serial; print('PyQt5', QtCore.QT_VERSION_STR, '| pyserial', serial.__version__, '| OK')"
```

期望：
- 能看到 `PyQt5 ... | pyserial ... | OK`

### 4.2 UI 生成链

```powershell
.\.venv\Scripts\pyuic5.exe --version
```

期望：
- 能输出 `Python User Interface Compiler ...`

### 4.3 Qt 平台插件

```powershell
.\.venv\Scripts\python.exe diagnose_qt_runtime.py
```

期望：
- `qwindows_exists=True`
- `qapplication=ok`

## 5. 你这次遇到的报错是什么意思

报错：

```text
qt.qpa.plugin: Could not find the Qt platform plugin "windows" in ""
This application failed to start because no Qt platform plugin could be initialized.
```

意思是：
- PyQt5 主包已经装了；
- 但 Qt 在启动 GUI 时，没有成功定位到 `platforms/qwindows.dll`；
- 或者找到了 `qwindows.dll`，但它的依赖 DLL 没进搜索路径。

这个工程现在已经做了两层修复：
- `bootstrap_qt.py` 会在 `QApplication` 创建前显式设置：
  - `QT_PLUGIN_PATH`
  - `QT_QPA_PLATFORM_PLUGIN_PATH`
  - Qt `bin` 目录到 `PATH`
- `launch_pc_host.ps1` 会固定使用项目 `.venv` 里的解释器启动。

## 6. 如果还报同样的错，按这个顺序排

### 6.1 确认你不是在系统 Python 下启动

运行：

```powershell
Get-Command python | Select-Object Source
```

如果不是项目 `.venv`，不要继续用裸 `python app.py`，改用：

```powershell
.\launch_pc_host.ps1
```

### 6.2 确认 `qwindows.dll` 存在

运行：

```powershell
Get-ChildItem .\.venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms
```

必须能看到：
- `qwindows.dll`

### 6.3 重新安装 GUI 依赖

```powershell
.\.venv\Scripts\python.exe -m pip uninstall -y PyQt5 PyQt5-Qt5 PyQt5-sip
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PyQt5
```

### 6.4 跑诊断脚本

```powershell
.\.venv\Scripts\python.exe diagnose_qt_runtime.py
```

如果这一步失败，把完整输出保留下来。

## 7. 当前依赖策略说明

这次不再强依赖 `pyqt5-tools`。

原因：
- 它在当前 `Python 3.12` 环境里依赖链不稳定；
- 但我们实际只需要：
  - `PyQt5`
  - `pyserial`
  - `pyuic5`

而 `pyuic5` 已经包含在当前 `PyQt5` 安装里，可直接用。

## 8. 验收时建议流程

1. 进入 `pc_host/`
2. 激活 `.venv`
3. 运行 `diagnose_qt_runtime.py`
4. 运行 `.\launch_pc_host.ps1`
5. 接上板子后检查：
   - COM 自动扫描
   - 连接/断开
   - 控制面板发送命令
   - 日志区收发
   - 数字孪生跟随 `*EVT:DISP / *EVT:LED`
   - 虚拟按键发送 `*SET:KEY`
