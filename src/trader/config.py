"""Settings, limits and secrets loading.

Secrets come from `.env` (never logged). Everything else comes from `config/`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Settings:
    lake_dir: Path
    raw_dir: Path
    quality_dir: Path
    history_start: date
    holdout_start: date
    holdout_end: date
    stock_bps: float
    etf_bps: float
    stress_multiplier: float
    scoring_model: str
    scoring_model_cutoff: str
    prompt_version: str
    llm_monthly_budget_usd: float
    live_mode: str
    raw: dict

    @property
    def holdout_start_ts(self) -> datetime:
        """Holdout begins at midnight New York time on holdout_start (UTC-aware)."""
        return datetime.combine(self.holdout_start, datetime.min.time(), NY).astimezone(UTC)

    @property
    def holdout_end_ts(self) -> datetime:
        return datetime.combine(self.holdout_end, datetime.min.time(), NY).astimezone(UTC)


@lru_cache(maxsize=1)
def settings() -> Settings:
    cfg = tomllib.loads((ROOT / "config" / "settings.toml").read_text(encoding="utf-8"))
    d, c, llm, live = cfg["data"], cfg["costs"], cfg["llm"], cfg["live"]
    return Settings(
        lake_dir=ROOT / d["lake_dir"],
        raw_dir=ROOT / d["raw_dir"],
        quality_dir=ROOT / d.get("quality_dir", "data/quality"),
        history_start=date.fromisoformat(d["history_start"]),
        holdout_start=date.fromisoformat(d["holdout_start"]),
        holdout_end=date.fromisoformat(d["holdout_end"]),
        stock_bps=float(c["stock_bps_per_side"]),
        etf_bps=float(c["etf_bps_per_side"]),
        stress_multiplier=float(c["stress_multiplier"]),
        scoring_model=llm["scoring_model"],
        scoring_model_cutoff=llm["scoring_model_cutoff"],
        prompt_version=llm["prompt_version"],
        llm_monthly_budget_usd=float(llm.get("monthly_budget_usd", 3.0)),
        live_mode=live["mode"],
        raw=cfg,
    )


@lru_cache(maxsize=1)
def limits() -> dict:
    return yaml.safe_load((ROOT / "config" / "limits.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def universe_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))


def secret(name: str, required: bool = True) -> str:
    """Read a secret from the environment / .env. Never log the return value."""
    load_dotenv(ROOT / ".env", override=False)
    val = os.environ.get(name, "").strip().strip('"')
    if required and not val:
        raise RuntimeError(f"Missing required secret {name} in .env")
    return val
