@echo off
cd /d "%~dp0"
title Shoken Analyzer

rem ---------------------------------------------------------------
rem  This file must stay ASCII-only with CRLF line endings.
rem  Japanese text here breaks cmd.exe on CP932 machines.
rem ---------------------------------------------------------------

if not exist "app\dashboard.py" goto wrong_folder

set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto have_python
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"

:have_python
if not defined PY goto no_python

if exist ".venv\Scripts\streamlit.exe" goto run
if exist ".venv\Scripts\python.exe" goto install

echo.
echo [1/3] Creating a private Python environment in .venv ...
echo       Kankyo wo sakusei shiteimasu.
%PY% -m venv .venv
if errorlevel 1 goto venv_failed

:install
echo.
echo [2/3] Installing libraries. This takes a few minutes on first run.
echo       Raiburari wo install shiteimasu. Shibaraku omachi kudasai.
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed

:run
echo.
echo [3/3] Starting the dashboard. Your browser will open in a moment.
echo       Dashboard wo kidou shimasu. Browser ga hirakimasu.
echo.
echo       To stop: close this black window.
echo.
".venv\Scripts\python.exe" -m streamlit run app/dashboard.py
goto end

:wrong_folder
echo.
echo [ERROR] app\dashboard.py was not found next to this file.
echo         start.bat must stay INSIDE the shoken_analyzer folder.
echo         Move the whole shoken_analyzer folder, not just this file.
goto end

:no_python
echo.
echo [ERROR] Python was not found on this PC.
echo.
echo         1. Open  https://www.python.org/downloads/windows/
echo         2. Install Python 3.11 or newer.
echo         3. IMPORTANT: tick "Add python.exe to PATH" in the installer.
echo         4. Run start.bat again.
goto end

:venv_failed
echo.
echo [ERROR] Could not create the .venv folder.
echo         Delete the .venv folder if it exists, then run start.bat again.
echo         If the folder is under OneDrive, try moving it to C:\ first.
goto end

:pip_failed
echo.
echo [ERROR] Could not install the libraries.
echo         This is usually a company network / proxy restriction.
echo         Ask IT to allow  https://pypi.org  and  https://files.pythonhosted.org
goto end

:end
echo.
pause
