@echo off
setlocal

set "APP_DIR=%~dp0"
set "PORT=8605"
set "URL=http://127.0.0.1:%PORT%/"
set "PYTHON_EXE=C:\Users\86155\miniconda3\python.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$connections = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; foreach ($conn in $connections) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match '--port %PORT%' -and $_.CommandLine -match 'webui[\\/]+server\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

start "Web-VLMAgent Workbench Server" /D "%APP_DIR%" /min cmd /k ""%PYTHON_EXE%" webui\server.py --host 127.0.0.1 --port %PORT%"
timeout /t 2 /nobreak >nul
start "" "%URL%"
exit /b 0
