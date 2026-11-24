@echo off
setlocal
set "LV_INSTALL="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\LinguaVoice" /v InstallPath 2^>nul') do if "%%A"=="REG_SZ" set "LV_INSTALL=%%B"
if not defined LV_INSTALL exit /b 2
if not exist "%LV_INSTALL%\.venv\Scripts\python.exe" exit /b 3
"%LV_INSTALL%\.venv\Scripts\python.exe" "%LV_INSTALL%\run_mcp.py"
exit /b %errorlevel%
