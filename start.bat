@echo off
rem 本文件为 UTF-8(无 BOM) + CRLF 保存；批处理对 LF 换行解析会错位，改动后请保持 CRLF
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title 评论观点智能分析 - 启动器
cd /d "%~dp0"

rem ============================================================
rem  评论观点智能分析 - 快捷启动脚本
rem
rem  用法：
rem    start.bat            单端口启动（推荐）：后端托管已构建前端，http://127.0.0.1:8000
rem    start.bat dev        开发模式：后端 --reload + 前端 Vite 热更新，http://127.0.0.1:5173
rem    start.bat build      只重新构建前端（依赖有变化时用）
rem    start.bat stop       停止本项目的后端 / 前端开发服务
rem    start.bat help       查看帮助
rem
rem  可选参数（可与上面的命令组合）：
rem    --no-browser         启动后不自动打开浏览器
rem ============================================================

set "MODE=%~1"
if "%MODE%"=="" set "MODE=start"
set "NO_BROWSER=0"
for %%A in (%*) do if /i "%%~A"=="--no-browser" set "NO_BROWSER=1"
if /i "%MODE%"=="--no-browser" set "MODE=start"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if /i "%MODE%"=="help" goto :help
if /i "%MODE%"=="-h" goto :help
if /i "%MODE%"=="stop" goto :stop
if /i "%MODE%"=="build" goto :build_only
if /i "%MODE%"=="dev" goto :dev
if /i "%MODE%"=="start" goto :start
echo [错误] 不认识的参数：%MODE%
goto :help


rem ============================================================ 单端口启动
:start
echo ============================================================
echo   评论观点智能分析 - 启动
echo ============================================================
echo.
call :check_python || goto :fail
call :check_env    || goto :fail
call :check_frontend_build || goto :fail

call :port_in_use %BACKEND_PORT%
if "!PORT_USED!"=="1" (
    echo [提示] %BACKEND_PORT% 端口已有服务在运行，直接打开页面。
) else (
    echo [1/2] 启动后端服务（新窗口显示日志）...
    start "评论分析-后端 (%BACKEND_PORT%)" cmd /k ""%VENV_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port %BACKEND_PORT%"
)

echo [2/2] 等待服务就绪...
call :wait_health %BACKEND_PORT%
if errorlevel 1 (
    echo [错误] 等待服务就绪超时，请查看“评论分析-后端”窗口中的日志。
    goto :fail
)
echo       服务已就绪：%BACKEND_URL%

call :open_browser "%BACKEND_URL%"
echo.
echo ============================================================
echo   启动完成
echo     上传分析：%BACKEND_URL%
echo     任务记录：%BACKEND_URL%/tasks
echo     接口文档：%BACKEND_URL%/docs
echo   停止服务：start.bat stop
echo ============================================================
goto :end


rem ============================================================ 开发模式
:dev
echo ============================================================
echo   评论观点智能分析 - 开发模式
echo ============================================================
echo.
call :check_python || goto :fail
call :check_env    || goto :fail
call :find_npm     || goto :fail
call :check_frontend_deps || goto :fail

call :port_in_use %BACKEND_PORT%
if "!PORT_USED!"=="1" (
    echo [提示] %BACKEND_PORT% 端口已有服务在运行，跳过后端启动。
) else (
    echo [1/3] 启动后端（--reload，改代码自动重启）...
    start "评论分析-后端 (%BACKEND_PORT%)" cmd /k ""%VENV_PY%" -m uvicorn backend.main:app --reload --host 127.0.0.1 --port %BACKEND_PORT%"
)

call :port_in_use %FRONTEND_PORT%
if "!PORT_USED!"=="1" (
    echo [提示] %FRONTEND_PORT% 端口已有服务在运行，跳过前端启动。
) else (
    echo [2/3] 启动前端开发服务器...
    start "评论分析-前端 (%FRONTEND_PORT%)" cmd /k "cd /d "%~dp0frontend" && "%NPM_CMD%" run dev"
)

echo [3/3] 等待前后端就绪...
call :wait_health %BACKEND_PORT%
if errorlevel 1 echo [警告] 后端 %BACKEND_PORT% 暂未就绪，请查看对应窗口日志。
call :wait_port %FRONTEND_PORT%
if errorlevel 1 echo [警告] 前端 %FRONTEND_PORT% 暂未就绪，请查看对应窗口日志。

call :open_browser "%FRONTEND_URL%"
echo.
echo ============================================================
echo   开发模式已启动
echo     前端（开发）：%FRONTEND_URL%
echo     后端接口文档：%BACKEND_URL%/docs
echo   停止服务：start.bat stop
echo ============================================================
goto :end


rem ============================================================ 只构建前端
:build_only
call :find_npm || goto :fail
call :check_frontend_deps || goto :fail
echo [构建] 正在构建前端（frontend/dist）...
pushd "%~dp0frontend"
"%NPM_CMD%" run build
set "BUILD_RC=%ERRORLEVEL%"
popd
if not "%BUILD_RC%"=="0" (
    echo [错误] 前端构建失败，请查看上面的日志。
    goto :fail
)
echo [完成] 前端已构建，start.bat 可以直接使用。
goto :end


rem ============================================================ 停止服务
:stop
echo ============================================================
echo   停止本项目的服务
echo ============================================================
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
echo 完成。
goto :end


rem ============================================================ 检查与工具
:check_python
if exist "%VENV_PY%" goto :check_python_deps
echo [提示] 未找到虚拟环境 .venv，正在用 uv 创建并安装依赖...
where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv，请先安装 uv（https://docs.astral.sh/uv/）后重试。
    exit /b 1
)
call uv sync
if errorlevel 1 exit /b 1
if not exist "%VENV_PY%" (
    echo [错误] uv sync 之后仍未找到 %VENV_PY%
    exit /b 1
)
:check_python_deps
"%VENV_PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [提示] 后端依赖不完整，正在用 uv sync 安装...
    where uv >nul 2>nul
    if errorlevel 1 (
        echo [错误] 缺少后端依赖且未找到 uv，请先运行 uv sync。
        exit /b 1
    )
    call uv sync
    if errorlevel 1 exit /b 1
)
exit /b 0

:check_env
if not exist "%~dp0.env" (
    echo [错误] 未找到 .env 配置文件。请复制下面的内容到项目根目录的 .env：
    echo         BASE_URL=https://你的接口地址/v1/chat/completions
    echo         API_KEY=sk-xxxx
    echo         MODEL_NAME=你的模型名
    exit /b 1
)
findstr /r /c:"^BASE_URL=." /c:"^API_KEY=." /c:"^MODEL_NAME=." "%~dp0.env" >nul 2>nul
if errorlevel 1 (
    echo [警告] .env 中 BASE_URL / API_KEY / MODEL_NAME 可能未填写完整，分析任务会失败。
)
exit /b 0

:find_npm
set "NPM_CMD="
rem 1) 最可靠：与 node.exe 同目录的 npm.cmd（PATH 里的 npm 可能是损坏的占位脚本，
rem    它会把自身路径解析到当前目录，导致 npm run 报 Cannot find module）
for /f "delims=" %%N in ('where node 2^>nul') do (
    if not defined NPM_CMD if exist "%%~dpNnpm.cmd" call :try_npm "%%~dpNnpm.cmd"
)
rem 2) PATH 中的 npm
if not defined NPM_CMD call :try_npm "npm"
rem 3) 常见安装目录兜底
if not defined NPM_CMD for %%P in (
    "E:\Software\Nodejs\npm.cmd"
    "%ProgramFiles%\nodejs\npm.cmd"
    "%ProgramFiles(x86)%\nodejs\npm.cmd"
    "%LocalAppData%\Programs\nodejs\npm.cmd"
) do if exist %%P call :try_npm "%%~P"
if not defined NPM_CMD (
    echo [错误] 未找到可用的 npm，请安装 Node.js 18+ 并确保 npm 可用。
    exit /b 1
)
echo [提示] 使用 npm：%NPM_CMD%
exit /b 0

:try_npm
set "TRY_VER="
for /f "delims=" %%V in ('%~1 --version 2^>nul') do set "TRY_VER=%%V"
if not defined TRY_VER exit /b 0
rem 版本号形如 10.9.3：按点拆成三段来判断，避免 findstr 不支持 [0-9] 区间写法
for /f "tokens=1,2,3 delims=." %%A in ("!TRY_VER!") do (
    if not "%%C"=="" set "NPM_CMD=%~1"
)
exit /b 0

:check_frontend_deps
if exist "%~dp0frontend\node_modules" exit /b 0
call :find_npm || exit /b 1
echo [提示] 未找到前端依赖，正在执行 npm install（首次较慢）...
pushd "%~dp0frontend"
"%NPM_CMD%" install --no-fund --no-audit
set "INSTALL_RC=%ERRORLEVEL%"
popd
if not "%INSTALL_RC%"=="0" (
    echo [错误] npm install 失败。
    exit /b 1
)
exit /b 0

:check_frontend_build
if exist "%~dp0frontend\dist\index.html" exit /b 0
echo [提示] 未找到已构建的前端（frontend/dist），正在构建...
call :check_frontend_deps || exit /b 1
pushd "%~dp0frontend"
"%NPM_CMD%" run build
set "BUILD_RC=%ERRORLEVEL%"
popd
if not "%BUILD_RC%"=="0" (
    echo [错误] 前端构建失败。
    exit /b 1
)
exit /b 0

:port_in_use
set "PORT_USED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do set "PORT_USED=1"
exit /b 0

:kill_port
set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do (
    call :kill_pid %%P
    set "KILLED=1"
)
call :port_in_use %~1
if "!PORT_USED!"=="1" (
    rem --reload 模式下父进程会自动拉起子进程，这里连同父进程一起结束
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do call :kill_parent %%P
    powershell -NoProfile -Command "Start-Sleep -Milliseconds 800" >nul 2>nul
    call :port_in_use %~1
)
if "!PORT_USED!"=="1" (
    echo   [警告] 端口 %~1 仍被占用，请尝试用管理员身份运行，或手动结束该进程。
) else if "!KILLED!"=="1" (
    echo   已停止端口 %~1 上的服务
) else (
    echo   端口 %~1 上未发现运行中的服务
)
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

:wait_port
rem Vite 首次启动需要预构建依赖，等待时间放宽到约 60 秒
powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%~1);$c.Close();exit 0}catch{};Start-Sleep -Milliseconds 500};exit 1"
exit /b %ERRORLEVEL%

:wait_health
powershell -NoProfile -Command "for($i=0;$i -lt 90;$i++){try{if((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%~1/api/health).StatusCode -eq 200){exit 0}}catch{};Start-Sleep -Milliseconds 700};exit 1"
exit /b %ERRORLEVEL%

:open_browser
if "%NO_BROWSER%"=="1" (
    echo       已跳过自动打开浏览器。
    exit /b 0
)
start "" "%~1"
exit /b 0


rem ============================================================ 帮助与收尾
:help
echo.
echo 评论观点智能分析 - 快捷启动脚本
echo.
echo   start.bat                 单端口启动（后端托管已构建前端）
echo   start.bat dev             开发模式（后端 reload + 前端热更新）
echo   start.bat build           只重新构建前端
echo   start.bat stop            停止本项目的后端 / 前端服务
echo   start.bat help            显示本帮助
echo.
echo   追加 --no-browser 可以不自动打开浏览器，例如：
echo       start.bat dev --no-browser
echo.
echo   默认地址：http://127.0.0.1:8000   （开发模式前端：http://127.0.0.1:5173）
echo.
goto :end

:fail
echo.
echo [失败] 启动未完成，请根据上面的提示处理后重试。
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
