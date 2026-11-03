"""Alerts: always appended to live/logs/alerts.log; pushed to ntfy.sh when NTFY_TOPIC is set."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from trader.config import ROOT, secret

LOG = ROOT / "live" / "logs" / "alerts.log"
PRIORITY = {"info": "2", "warning": "3", "error": "4", "critical": "5"}


def notify(level: str, message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), "level": level, "msg": message}) + "\n")
    topic = secret("NTFY_TOPIC", required=False)
    if not topic or level == "info":
        return
    try:
        httpx.post(f"https://ntfy.sh/{topic}", content=message.encode(), timeout=10,
                   headers={"Title": f"trader {level}", "Priority": PRIORITY.get(level, "3")})
    except httpx.HTTPError:
        pass  # alerting must never break trading; the local log has it
