"""Launches the read-only Alpaca MCP server with the DATA keys from .env (any OS). Referenced by .mcp.json.

Toolsets exclude account/trading/watchlists/locates (CLAUDE.md rule 1). Standard library only, and
nothing is printed to stdout: stdout carries the MCP protocol.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
vals = {}
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
    if m:
        vals[m[1]] = m[2].strip().strip('"')

env = {
    **os.environ,
    "ALPACA_API_KEY": vals.get("ALPACA_DATA_KEY", ""),
    "ALPACA_SECRET_KEY": vals.get("ALPACA_DATA_SECRET", ""),
    "ALPACA_PAPER_TRADE": "true",
    "ALPACA_TOOLSETS": "stock-data,news,assets,corporate-actions",
}
uvx = shutil.which("uvx")
if not uvx:
    sys.exit("uvx not found on PATH (it ships with uv)")
sys.exit(subprocess.run([uvx, "alpaca-mcp-server==2.4.0"], env=env).returncode)
