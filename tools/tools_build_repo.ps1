# 建仓库并推送到 GitHub
$ErrorActionPreference = "Stop"
$SRC = "D:\DP workspace\btc_system"
$REPO = "D:\DP workspace\BTC决策交易系统"
$SKILL = "$REPO\skill\btc-trading-agent"

if (Test-Path $REPO) { Remove-Item $REPO -Recurse -Force }
New-Item -ItemType Directory -Path "$REPO\core", "$REPO\tools", "$REPO\data", "$REPO\docs", "$SKILL\scripts", "$SKILL\references" -Force | Out-Null

# ---------- 顶层代码 ----------
foreach ($f in @("run.py","perf.py","review.py","report.py","render_card.py","diag.py",
                 "backtest.py","flatten.py","self_test.py","run_bot.cmd")) {
    if (Test-Path "$SRC\$f") { Copy-Item "$SRC\$f" "$REPO\" }
}
Copy-Item "$SRC\core\*.py" "$REPO\core\"

# ---------- 运维脚本 ----------
foreach ($f in @("restart_bot.ps1","run_if_missing.ps1","run_if_missing.vbs")) {
    if (Test-Path "$SRC\tools\$f") { Copy-Item "$SRC\tools\$f" "$REPO\tools\" }
}
foreach ($f in @("tools_go_hedge.py","tools_fix_stops.py","tools_add_size.py","tools_flat_all.py","tools_pack.ps1")) {
    if (Test-Path "$SRC\$f") { Copy-Item "$SRC\$f" "$REPO\tools\" }
}

# ---------- SKILL 包（自包含：把代码也放进去）----------
Copy-Item "$SRC\core\*.py" "$SKILL\scripts\core\" -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$SKILL\scripts\core" -Force | Out-Null
Copy-Item "$SRC\core\*.py" "$SKILL\scripts\core\"
foreach ($f in @("run.py","perf.py","review.py","render_card.py","diag.py","backtest.py","flatten.py")) {
    Copy-Item "$SRC\$f" "$SKILL\scripts\"
}

# ---------- 模板 ----------
$cfg = Get-Content "$SRC\config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$cfg | ConvertTo-Json -Depth 6 | Set-Content "$REPO\config.example.json" -Encoding UTF8
Copy-Item "$REPO\config.example.json" "$SKILL\scripts\config.example.json"

"" | Set-Content "$REPO\data\.gitkeep" -Encoding UTF8

Write-Host "骨架建好: $REPO"
Write-Host "已拷贝文件数: $((Get-ChildItem $REPO -Recurse -File).Count)"
