@echo off
setlocal
set ROOT=%~dp0
set PYTHON=%ROOT%\.venv\Scripts\python.exe
if not exist "%PYTHON%" (
  echo 未找到虚拟环境解释器: %PYTHON%
  echo 请先按 README 或 docs\deployment.md 创建 .venv
  exit /b 1
)
pushd "%ROOT%"
"%PYTHON%" ".\app.py"
popd
endlocal
