# Restart / inspect the BTC strategy.
#
# IMPORTANT: the bot must be owned by Task Scheduler ("BTC_Watchdog"), because
# any process this shell spawns directly gets reaped when the shell's job ends.
# So a "restart" here means: kill the old python, then ask the task to start it
# again in the foreground (the task stays Running while the bot lives).

param(
    [switch]$StatusOnly,
    [switch]$Restart
)

$ErrorActionPreference = "SilentlyContinue"
$root = "D:\DP workspace\btc_system"
$task = "BTC_Watchdog"
$log  = Join-Path $root ("logs\btc-" + (Get-Date -Format 'yyyyMMdd') + ".log")

function Get-BotProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*run.py*" }
}

function Show-Status {
    $p = Get-BotProc
    if ($p) {
        $lag = -1
        if (Test-Path $log) {
            $age = (Get-Date) - (Get-Item $log).LastWriteTime
            $lag = [int]$age.TotalSeconds
        }
        $mem = [math]::Round($p.WorkingSetSize / 1MB, 0)
        Write-Host ("RUNNING  pid=" + $p.ProcessId + "  mem=" + $mem + "MB  log_lag=" + $lag + "s")
        if ($lag -gt 300) { Write-Host "WARN: log older than 5 minutes, bot may be stuck" }
    } else {
        Write-Host "STOPPED"
    }
    $t = (schtasks /query /tn $task /fo list 2>$null) -join "`n"
    if ($t -match "Running") { Write-Host ("TASK     " + $task + " = Running") }
    elseif ($t -match "Ready") { Write-Host ("TASK     " + $task + " = Ready (will auto-start within 5 min)") }
    else { Write-Host ("TASK     " + $task + " = not found") }
}

if ($StatusOnly) { Show-Status; return }

if ($Restart) {
    Get-BotProc | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 4
    Write-Host "old process stopped"
}

if (Get-BotProc) {
    Write-Host "already running (use -Restart to force)"
    Show-Status
    return
}

schtasks /run /tn $task | Out-Null
Start-Sleep -Seconds 12
if (Get-BotProc) {
    Write-Host "started via task scheduler"
    Show-Status
} else {
    Write-Host "FAILED to start; check logs\stdout.log and logs\stderr.log"
    if (Test-Path (Join-Path $root "logs\stderr.log")) {
        Get-Content (Join-Path $root "logs\stderr.log") -Tail 20
    }
}
