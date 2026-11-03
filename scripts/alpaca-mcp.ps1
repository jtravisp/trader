# Launches the read-only Alpaca MCP server with the DATA keys read from .env (no user env vars).
# Referenced by .mcp.json. Toolsets exclude account/trading/watchlists/locates (CLAUDE.md rule 1).
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root '.env'
$vals = @{}
foreach ($line in Get-Content $envPath) {
  if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $vals[$Matches[1]] = $Matches[2].Trim().Trim('"') }
}
$env:ALPACA_API_KEY = $vals['ALPACA_DATA_KEY']
$env:ALPACA_SECRET_KEY = $vals['ALPACA_DATA_SECRET']
$env:ALPACA_PAPER_TRADE = 'true'
$env:ALPACA_TOOLSETS = 'stock-data,news,assets,corporate-actions'
& uvx 'alpaca-mcp-server==2.4.0'
exit $LASTEXITCODE
