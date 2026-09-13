@echo off
title Dexcom Webserver
cd /d "%~dp0"

echo ========================================================
echo        DEXCOM WEBSERVER - START OCH KONTROLL
echo ========================================================
echo.

REM 1. Kontrollera om Python finns
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [1/3] Python saknas. Installerar Python automatiskt...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo Forsoker ladda ner Python-installerare...
        powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%temp%\python-installer.exe'; Start-Process -FilePath '%temp%\python-installer.exe' -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1' -Wait; Remove-Item '%temp%\python-installer.exe' -Force"
    )
    for /f "tokens=*" %%i in ('powershell -Command "[System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')"') do set "PATH=%%i"
) else (
    echo [1/3] Python hittades!
)

REM 2. Kontrollera och installera Python-paket
echo.
echo [2/3] Kontrollerar Python-paket (flask, pydexcom, pychromecast)...
python -m pip install flask pydexcom pychromecast --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo Ett fel uppstod vid installation av Python-paket.
    pause
    exit /b 1
)
echo [OK] Paket ar redo!

REM 3. Starta servern
echo.
echo [3/3] Startar Dexcom Webserver pa port 8080...
echo.
python dexcom_webserver.py

if %errorlevel% neq 0 (
    echo.
    echo Programmet avslutades med ett fel.
    pause
)
