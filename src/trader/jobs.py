"""Scheduled jobs, the same on every OS.

Windows Task Scheduler (scripts/register-tasks.ps1) and macOS launchd (scripts/macos/install-launchd.sh)
both run `uv run --no-sync trader job <name>`. Each step runs as its own process, so a failing step
never skips the later ones, and all output is appended as UTF-8 to data/logs/<name>_<NY date>.log.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from trader.config import NY, ROOT

# name -> steps (trader CLI args). The job fails with the first failing step's exit code.
JOBS: dict[str, list[list[str]]] = {
    # evening (19:00 ET, daily): forward data + LLM scoring, the daily report, tomorrow's target
    "forward": [["forward"], ["live", "report"], ["live", "target"]],
    # morning (09:40 ET, weekdays): the risk gateway executes the latest target. Outside the
    # trading window or on a holiday it sees the market closed and does nothing.
    "gateway": [["live", "execute", "--submit"]],
}


def log_path(name: str, now: datetime | None = None, root: Path = ROOT) -> Path:
    day = (now or datetime.now(NY)).astimezone(NY).date()
    return root / "data" / "logs" / f"{name}_{day.isoformat()}.log"


def run(name: str, root: Path = ROOT, python: str = sys.executable) -> int:
    if name not in JOBS:
        raise ValueError(f"unknown job {name!r}; expected one of {sorted(JOBS)}")
    path = log_path(name, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    codes = []
    with path.open("ab") as f:
        for args in JOBS[name]:
            f.write(f"[{datetime.now(NY):%H:%M:%S}] job {name}: trader {' '.join(args)}\n".encode())
            f.flush()
            p = subprocess.run([python, "-m", "trader", *args], cwd=root, env=env, stdout=f, stderr=subprocess.STDOUT)
            codes.append(p.returncode)
        f.write(f"[{datetime.now(NY):%H:%M:%S}] job {name}: exit codes {codes}\n".encode())
    return next((c for c in codes if c != 0), 0)
