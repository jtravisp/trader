"""Rate-limited HTTP clients with retries and raw-response archiving."""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from trader.config import secret, settings


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float) -> None:
        self.max_calls, self.per = max_calls, per_seconds
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > self.per:
                self.calls.popleft()
            if len(self.calls) >= self.max_calls:
                time.sleep(self.per - (now - self.calls[0]) + 0.01)
            self.calls.append(time.monotonic())


class RawArchive:
    """Append-only gzip JSONL archive of raw responses: data/raw/<source>/<YYYY-MM-DD>/<name>.jsonl.gz"""

    def __init__(self, source: str, name: str) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        self.path: Path = settings().raw_dir / source / day / f"{name}.jsonl.gz"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, request: dict[str, Any], payload: Any) -> None:
        rec = {"fetched_at": datetime.now(UTC).isoformat(), "request": request, "payload": payload}
        with gzip.open(self.path, "at", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


class HttpError(RuntimeError):
    """HTTP failure with the query string removed (it may carry API keys, rule 7)."""


def _safe_raise(r: httpx.Response) -> None:
    if r.is_success:
        return
    url = str(r.request.url).split("?")[0]
    raise HttpError(f"HTTP {r.status_code} for {url}: {r.text[:300]}")


class Http:
    def __init__(self, base_url: str, headers: dict[str, str], limiter: RateLimiter, timeout: float = 60) -> None:
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self.limiter = limiter

    def get(self, path: str, params: dict[str, Any] | None = None, *, retries: int = 6) -> httpx.Response:
        delay = 2.0
        for attempt in range(retries):
            self.limiter.wait()
            try:
                r = self.client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries - 1:
                    _safe_raise(r)
                time.sleep(float(r.headers.get("retry-after", delay)))
                delay *= 2
                continue
            return r
        raise RuntimeError("unreachable")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = self.get(path, params)
        _safe_raise(r)
        return r.json()


_alpaca_limiter = RateLimiter(190, 60.0)  # free tier: 200/min
_sec_limiter = RateLimiter(8, 1.0)  # SEC: max 10/s


def alpaca_data() -> Http:
    h = {"APCA-API-KEY-ID": secret("ALPACA_DATA_KEY"), "APCA-API-SECRET-KEY": secret("ALPACA_DATA_SECRET")}
    return Http("https://data.alpaca.markets", h, _alpaca_limiter)


def alpaca_paper_readonly() -> Http:
    """Paper trading API with DATA keys, used only for read-only reference endpoints (assets)."""
    h = {"APCA-API-KEY-ID": secret("ALPACA_DATA_KEY"), "APCA-API-SECRET-KEY": secret("ALPACA_DATA_SECRET")}
    return Http("https://paper-api.alpaca.markets", h, _alpaca_limiter)


def sec(host: str = "https://data.sec.gov") -> Http:
    ua = secret("SEC_USER_AGENT")
    return Http(host, {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, _sec_limiter, timeout=120)


def paginate(http: Http, path: str, params: dict[str, Any], archive: RawArchive | None = None) -> Iterator[dict]:
    """Iterate Alpaca pages following next_page_token."""
    params = dict(params)
    while True:
        page = http.get_json(path, params)
        if archive is not None:
            archive.append({"path": path, "params": params}, page)
        yield page
        tok = page.get("next_page_token")
        if not tok:
            return
        params["page_token"] = tok
