@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Please read README.md and docs\INSTALL.md.
  pause
  exit /b 1
)
if not exist "private" mkdir "private"
powershell -NoProfile -Command "$logId=[Guid]::NewGuid().ToString('N'); Start-Process -WindowStyle Hidden -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','linguavoice.server','--open' -WorkingDirectory (Get-Location).Path -RedirectStandardOutput ('private\console-'+$logId+'.log') -RedirectStandardError ('private\console-'+$logId+'-error.log')"
