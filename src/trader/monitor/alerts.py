"""Alerts: always appended to live/logs/alerts.log; pushed to ntfy.sh when NTFY_TOPIC is set
(authenticated with NTFY_TOKEN when present)."""

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
    send(f"trader {level}", message, PRIORITY.get(level, "3"))


def send(title: str, message: str, priority: str = "3") -> int | None:
    """Push to ntfy. Returns the HTTP status, or None on network failure (never raises)."""
    topic = secret("NTFY_TOPIC", required=False)
    if not topic:
        return None
    headers = {"Title": title, "Priority": priority}
    token = secret("NTFY_TOKEN", required=False)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return httpx.post(f"https://ntfy.sh/{topic}", content=message.encode(), timeout=10, headers=headers).status_code
    except httpx.HTTPError:
        return None  # alerting must never break trading; the local log has it


def heartbeat(job: str, ok: bool = True) -> None:
    """Dead-man's switch: ping healthchecks.io (or any compatible URL) when a scheduled job finishes.
    Configure HC_FORWARD_URL / HC_GATEWAY_URL in .env; the check alerts you when pings stop arriving
    (PC off, task broken), which ntfy alone can't detect. A no-op when unset; never raises."""
    url = secret(f"HC_{job.upper()}_URL", required=False)
    if not url:
        return
    try:
        httpx.get(url if ok else url.rstrip("/") + "/fail", timeout=10)
    except httpx.HTTPError:
        pass
