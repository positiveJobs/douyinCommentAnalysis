@echo off
rem 本文件为 UTF-8(无 BOM) + CRLF 保存；批处理对 LF 换行解析会错位，改动后请保持 CRLF
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title 评论观点智能分析 - 关闭
cd /d "%~dp0"

rem ============================================================
rem  评论观点智能分析 - 快捷关闭脚本
rem
rem  用法：
rem    stop.bat              停止本项目的后端 / 前端服务（可直接双击）
rem    stop.bat --no-pause   停止后不等待按键（供其他脚本调用）
rem
rem  关闭对象：8000 端口（后端 uvicorn）、5173 端口（前端开发服务器），
rem            以及本项目残留的 uvicorn / vite 进程和对应的命令行窗口。
rem  等价于 start.bat stop，但可单独双击运行。
rem ============================================================

set "NO_PAUSE=0"
for %%A in (%*) do if /i "%%~A"=="--no-pause" set "NO_PAUSE=1"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo ============================================================
echo   评论观点智能分析 - 关闭服务
echo ============================================================
echo.

call :kill_port %BACKEND_PORT% 后端
call :kill_port %FRONTEND_PORT% 前端

rem 兜底：清理本项目残留的 uvicorn / vite 进程
call :kill_leftover

rem 关掉 start.bat 打开的“后端 / 前端”命令行窗口
call :close_windows

echo.
echo ============================================================
echo   已关闭，端口 %BACKEND_PORT% / %FRONTEND_PORT% 均已释放
echo   重新启动：双击 start.bat
echo ============================================================

if "%NO_PAUSE%"=="1" goto :end
rem 双击运行时（cmdcmdline 里含本脚本名）暂停，方便看结果；在控制台内运行时直接退出
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 pause
goto :end


rem ============================================================ 按端口停止
:kill_port
set "KP_PORT=%~1"
set "KP_NAME=%~2"
set "KP_KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":!KP_PORT! .*LISTENING"') do (
    call :kill_pid %%P
    set "KP_KILLED=1"
)
call :port_in_use !KP_PORT!
if "!PORT_USED!"=="1" (
    rem --reload 模式下父进程会自动拉起子进程，这里连同父进程一起结束
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":!KP_PORT! .*LISTENING"') do call :kill_parent %%P
    powershell -NoProfile -Command "Start-Sleep -Milliseconds 800" >nul 2>nul
    call :port_in_use !KP_PORT!
)
if "!PORT_USED!"=="1" (
    echo   [警告] %KP_NAME% 端口 !KP_PORT! 仍被占用，请用管理员身份重试，或手动结束该进程。
) else if "!KP_KILLED!"=="1" (
    echo   已停止%KP_NAME%服务（端口 !KP_PORT!）
) else (
    echo   %KP_NAME%未在运行（端口 !KP_PORT! 空闲）
)
exit /b 0


rem ============================================================ 清理残留进程
:kill_leftover
rem 只结束“命令行里同时含本项目路径和 uvicorn / vite / backend.main / npm”的进程，
rem 避免误杀编辑器等其他 node 进程；受限环境下取不到命令行则自动跳过。
powershell -NoProfile -Command "$root = '%~dp0'.TrimEnd('\'); Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $root + '*') -and $_.CommandLine -match 'uvicorn|vite|backend\.main|npm' -and $_.Name -match '^(python|node|cmd)\.exe$' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
exit /b 0


rem ============================================================ 关闭命令行窗口
:close_windows
taskkill /FI "WINDOWTITLE eq 评论分析-后端*" >nul 2>nul
taskkill /FI "WINDOWTITLE eq 评论分析-前端*" >nul 2>nul
exit /b 0


rem ============================================================ 通用工具
:port_in_use
set "PORT_USED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do set "PORT_USED=1"
exit /b 0

:kill_pid
taskkill /PID %~1 /F >nul 2>nul
if errorlevel 1 (
    rem 部分受限环境下 taskkill 会被拒绝，退回用 PowerShell 结束进程
    powershell -NoProfile -Command "Stop-Process -Id %~1 -Force -ErrorAction SilentlyContinue" >nul 2>nul
)
exit /b 0

:kill_parent
for /f "usebackq tokens=2" %%Q in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%~1' -ErrorAction SilentlyContinue).ParentProcessId" 2^>nul`) do (
    if not "%%Q"=="0" call :kill_pid %%Q
)
exit /b 0


:end
endlocal
exit /b 0
