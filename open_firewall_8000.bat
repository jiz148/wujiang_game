@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_firewall_port.ps1" -Port 8000
endlocal
