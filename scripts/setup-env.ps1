<#
  Checks .env (the single source of secrets) and reports which keys are set. Never prints values.
  Copy .env.example to .env and fill it in with any editor; then run:
    powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
  Nothing is written to user environment variables: Python code reads .env via python-dotenv,
  and the Alpaca MCP server is launched by scripts\alpaca-mcp.ps1, which reads .env itself.
#>
$root    = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root '.env'
if (-not (Test-Path $envPath)) {
  Copy-Item (Join-Path $root '.env.example') $envPath
  Write-Host "Created .env from .env.example - fill it in, then re-run." -ForegroundColor Yellow
  exit 1
}
$vals = @{}
foreach ($line in Get-Content $envPath) {
  if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $vals[$Matches[1]] = $Matches[2].Trim().Trim('"') }
}
$required = 'ALPACA_DATA_KEY','ALPACA_DATA_SECRET','ALPACA_TRADE_KEY','ALPACA_TRADE_SECRET','ALPACA_TRADE_PAPER','TRADER_ANTHROPIC_API_KEY','SEC_USER_AGENT','FRED_API_KEY'
$optional = 'MASSIVE_API_KEY','NTFY_TOPIC'
$missing = 0
foreach ($k in $required + $optional) {
  $set = [bool]$vals[$k]
  if (-not $set -and $required -contains $k) { $missing++ }
  $state = if ($set) { 'set' } elseif ($required -contains $k) { 'MISSING' } else { 'empty (optional)' }
  Write-Host ("  {0,-26} {1}" -f $k, $state)
}
foreach ($k in 'ALPACA_DATA_KEY','ALPACA_DATA_SECRET') {
  if ([Environment]::GetEnvironmentVariable($k, 'User')) {
    Write-Host "Note: $k is also a user env var from the old setup; it is no longer needed." -ForegroundColor DarkGray
  }
}
if ($missing) { Write-Host "$missing required key(s) missing." -ForegroundColor Red; exit 1 }
Write-Host "All required keys present." -ForegroundColor Green
