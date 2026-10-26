# 打包成可分发的 GitHub 仓库（自动排除密钥、状态、日志）
$ErrorActionPreference = "Stop"
$SRC = "D:\DP workspace\btc_system"
$OUT = "D:\DP workspace\btc-agent-v1"

if (Test-Path $OUT) { Remove-Item $OUT -Recurse -Force }
New-Item -ItemType Directory -Path $OUT, "$OUT\core", "$OUT\tools", "$OUT\data", "$OUT\docs" -Force | Out-Null

$topFiles = @("run.py", "perf.py", "review.py", "report.py", "render_card.py",
              "diag.py", "backtest.py", "flatten.py", "self_test.py", "run_bot.cmd")
foreach ($f in $topFiles) {
    $p = Join-Path $SRC $f
    if (Test-Path $p) { Copy-Item $p "$OUT\" }
}
Copy-Item "$SRC\core\*.py" "$OUT\core\"

$toolFiles = @("restart_bot.ps1", "run_if_missing.ps1", "run_if_missing.vbs")
foreach ($f in $toolFiles) {
    $p = Join-Path $SRC "tools\$f"
    if (Test-Path $p) { Copy-Item $p "$OUT\tools\" }
}
foreach ($f in @("tools_go_hedge.py", "tools_fix_stops.py", "tools_add_size.py")) {
    $p = Join-Path $SRC $f
    if (Test-Path $p) { Copy-Item $p "$OUT\tools\" }
}

"" | Set-Content "$OUT\data\.gitkeep" -Encoding UTF8

# 从真实 config 生成脱敏模板
$cfg = Get-Content "$SRC\config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$cfg | ConvertTo-Json -Depth 6 | Set-Content "$OUT\config.example.json" -Encoding UTF8

Write-Host "打包完成: $OUT"
Get-ChildItem $OUT -Recurse -File | ForEach-Object {
    "{0,-42} {1,8:N1} KB" -f $_.FullName.Replace("$OUT\", ""), ($_.Length / 1KB)
}
