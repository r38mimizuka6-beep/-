@echo off
rem Internal launcher. Do not run this directly - use open_dashboard.vbs.
rem Starts the dashboard server with no console prompt and logs to logs\dashboard.log.
cd /d "%~dp0"
if not exist "logs" mkdir "logs"

rem Keep the log from growing without bound.
for %%F in ("logs\dashboard.log") do if %%~zF GTR 2000000 del "logs\dashboard.log"

if not exist ".venv\Scripts\python.exe" goto missing
echo [%date% %time%] starting >> "logs\dashboard.log"
".venv\Scripts\python.exe" -m streamlit run app/dashboard.py --server.headless true >> "logs\dashboard.log" 2>&1
echo [%date% %time%] stopped with code %errorlevel% >> "logs\dashboard.log"
exit /b %errorlevel%

:missing
echo [%date% %time%] .venv not found - run start.bat once first. >> "logs\dashboard.log"
exit /b 1
