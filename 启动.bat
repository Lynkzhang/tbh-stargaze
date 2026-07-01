@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYW=python"
set "PYDIR="
set "WHEELS=%ROOT%\wheels"

rem ---- Prefer portable Python if bundled ----
if exist "%ROOT%\python-portable\python.exe" (
    set "PYW=%ROOT%\python-portable\python.exe"
    set "PYDIR=%ROOT%\python-portable"
    echo [INFO] Using portable Python.
) else (
    echo [INFO] Using system Python.
)

rem ---- Verify python works ----
"%PYW%" --version >nul 2>&1
if errorlevel 1 goto err_no_python

rem ---- Quick frida import test ----
"%PYW%" -c "import frida" >nul 2>&1
if errorlevel 1 goto need_install
goto deps_ok

:need_install
echo.
echo ============================================================
echo  First-run setup: installing dependencies (one-time, ~30s)
echo ============================================================
echo.
if "%PYDIR%"=="" goto install_system
goto install_portable

:install_portable
if not exist "%PYDIR%\get-pip.py" (
    echo [ERROR] get-pip.py missing at %PYDIR%
    goto err_install
)
echo [1/2] Installing pip into portable Python...
"%PYDIR%\python.exe" "%PYDIR%\get-pip.py" --no-warn-script-location --no-index --find-links="%WHEELS%" 2>nul
if errorlevel 1 (
    echo [INFO] Offline install failed, trying Tsinghua mirror...
    "%PYDIR%\python.exe" "%PYDIR%\get-pip.py" --no-warn-script-location -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if errorlevel 1 goto err_install
)
echo [2/2] Installing frida and psutil from local wheels...
"%PYDIR%\python.exe" -m pip install --no-warn-script-location --no-index --find-links="%WHEELS%" frida psutil
if errorlevel 1 (
    echo [INFO] Offline wheel install failed, trying Tsinghua mirror...
    "%PYDIR%\python.exe" -m pip install --no-warn-script-location -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn frida psutil
    if errorlevel 1 goto err_install
)
goto post_install

:install_system
"%PYW%" -m pip install --user -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn frida psutil
if errorlevel 1 goto err_install
goto post_install

:post_install
"%PYW%" -c "import frida" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] frida still missing after install.
    pause
    exit /b 3
)
echo.
echo [OK] Setup complete. Continuing...
echo.
goto deps_ok

:deps_ok
rem ---- Check the game is running ----
tasklist /FI "IMAGENAME eq TaskBarHero.exe" 2>nul | find /I "TaskBarHero.exe" >nul
if errorlevel 1 (
    echo [WARN] TaskBarHero.exe is NOT running.
    echo        Please start the game first. The tool will wait.
    echo.
)

rem ---- Check admin rights ----
net session >nul 2>&1
if errorlevel 1 (
    echo [WARN] Not running as administrator. Frida attach may fail.
    echo        Right-click this file and choose Run as administrator.
    echo.
    timeout /t 3 >nul
)

rem ---- Launch browser ----
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:18765/"

echo.
echo ============================================================
echo  TBHStargaze starting... Browser will open automatically.
echo  Web UI:  http://127.0.0.1:18765/
echo  Press Ctrl+C to quit.
echo ============================================================
echo.

"%PYW%" "%ROOT%\src\tbh_reader.py" http --port 18765

echo.
echo [INFO] Stopped. Press any key to close.
pause >nul
exit /b 0

:err_no_python
echo [ERROR] Python not found.
echo         If you are using the portable bundle, the python-portable folder is missing.
echo         Otherwise install Python 3.10+ from python.org
pause
exit /b 1

:err_install
echo.
echo [ERROR] Dependency install failed. See messages above.
pause
exit /b 2