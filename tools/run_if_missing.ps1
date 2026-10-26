# Watchdog entry for Task Scheduler.
# Behaviour:
#   - if a run.py process already exists -> exit immediately (task completes fast)
#   - otherwise -> run the bot IN THE FOREGROUND so this task stays alive
#     (Task Scheduler's "do not start a new instance" prevents duplicates;
#      if we spawned a detached child and exited, the child would be reaped.)

$ErrorActionPreference = "SilentlyContinue"
$root = "D:\DP workspace\btc_system"
$py   = "C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

function Get-BotProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*run.py*" }
}

if (Get-BotProc) {
    Write-Host "bot already running, watchdog exits"
    exit 0
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"
$env:BTC_NO_CONSOLE   = "1"
Set-Location $root

# foreground: this process IS the task, so nothing gets reaped
& $py -u (Join-Path $root "run.py") *>> (Join-Path $root "logs\stdout.log")
