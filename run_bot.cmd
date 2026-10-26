@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set BTC_NO_CONSOLE=1
cd /d "D:\DP workspace\btc_system"
"C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -u run.py >> "logs\stdout.log" 2>&1
