"""Alerts: always appended to live/logs/alerts.log (combined feed) and live/<account>/logs/alerts.log;
pushed to ntfy.sh when NTFY_TOPIC is set (authenticated with NTFY_TOKEN when present).

Every alert is tagged with the account ([paper] / [LIVE], D36). Live alerts go out one priority higher."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from trader.config import ROOT, secret

LOG = ROOT / "live" / "logs" / "alerts.log"
PRIORITY = {"info": "2", "warning": "3", "error": "4", "critical": "5"}
ACCOUNT = "paper"  # set by trader.live.accounts.use()


def tag() -> str:
    return "[LIVE]" if ACCOUNT == "live" else f"[{ACCOUNT}]"


def priority(level: str) -> str:
    p = int(PRIORITY.get(level, "3"))
    return str(min(5, p + 1) if ACCOUNT == "live" else p)


def notify(level: str, message: str) -> None:
    rec = json.dumps({"ts": datetime.now(UTC).isoformat(), "level": level, "account": ACCOUNT,
                      "msg": f"{tag()} {message}"}) + "\n"
    for path in (LOG, LOG.parents[1] / ACCOUNT / "logs" / "alerts.log"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(rec)
    topic = secret("NTFY_TOPIC", required=False)
    if not topic or level == "info":
        return
    send(f"trader {level} {tag()}", f"{tag()} {message}", priority(level))


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
    Configure HC_FORWARD_URL / HC_GATEWAY_LIVE_URL / HC_GATEWAY_PAPER_URL in .env (HC_GATEWAY_URL still
    serves paper); the check alerts you when pings stop arriving (PC off, task broken), which ntfy alone
    can't detect. One ping per account, so a dead live run can't hide behind a healthy paper run (D36).
    A no-op when unset; never raises."""
    url = secret(f"HC_{job.upper()}_URL", required=False)
    if not url and job == "gateway_paper":
        url = secret("HC_GATEWAY_URL", required=False)
    if not url:
        return
    try:
        httpx.get(url if ok else url.rstrip("/") + "/fail", timeout=10)
    except httpx.HTTPError:
        pass
