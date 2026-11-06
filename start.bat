@echo off
rem Nhay dup de cai dat va khoi dong FraudLens (can Docker Desktop). Tham so duoc chuyen cho start.ps1, vd: start.bat -Monitoring
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
