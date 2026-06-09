@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0pc_host"
call ".\launch_pc_host.bat"
popd
endlocal
