"""SEC EDGAR ingestion: ticker map, filings index (incl. 8-K items), XBRL company facts.

edgar_filings: one row per (cik, accession). event_ts = report date (or acceptance), known_ts =
acceptance time. EDGAR's `acceptanceDateTime` is true UTC (verified 2026-11-03: Apple's 16:30 ET
earnings 8-Ks show 20:30Z in EDT and 21:30Z in EST).

edgar_facts: selected us-gaap/dei concepts. known_ts = acceptance time of the filing that
reported the value (join on accession), else end of the `filed` date in New York.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from trader.config import settings
from trader.data import lake
from trader.data.http import _safe_raise, sec

FILINGS = "edgar_filings"
FACTS = "edgar_facts"

CONCEPTS = {
    "us-gaap": [
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
        "CostOfRevenue", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
        "EarningsPerShareBasic", "EarningsPerShareDiluted",
        "WeightedAverageNumberOfDilutedSharesOutstanding", "IncomeTaxExpenseBenefit",
        "ResearchAndDevelopmentExpense", "Assets", "AssetsCurrent", "Liabilities",
        "LiabilitiesCurrent", "StockholdersEquity", "CashAndCashEquivalentsAtCarryingValue",
        "AccountsReceivableNetCurrent", "InventoryNet", "LongTermDebt", "LongTermDebtNoncurrent",
        "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
        "DepreciationDepletionAndAmortization", "PaymentsOfDividendsCommonStock",
        "PaymentsForRepurchaseOfCommonStock",
    ],
    "dei": ["EntityCommonStockSharesOutstanding", "EntityPublicFloat"],
}


def _cache_dir(kind: str) -> Path:
    d = settings().raw_dir / kind / datetime.now(UTC).strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_json(kind: str, name: str, host: str, path: str) -> dict | None:
    """Fetch once per day; raw response archived gzip under data/raw/<kind>/<day>/."""
    f = _cache_dir(kind) / f"{name}.json.gz"
    if f.exists():
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    r = sec(host).get(path)
    if r.status_code == 404:
        return None
    _safe_raise(r)
    data = r.json()
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def ticker_map() -> pl.DataFrame:
    data = _cached_json("sec_tickers", "company_tickers_exchange", "https://www.sec.gov", "/files/company_tickers_exchange.json")
    assert data is not None
    df = pl.DataFrame(data["data"], schema=data["fields"], orient="row")
    return df.select(pl.col("ticker").str.replace(r"-", ".").alias("ticker"), pl.col("cik").cast(pl.Int64), "exchange").unique("ticker")


def submissions(cik: int) -> dict | None:
    return _cached_json("sec_submissions", f"CIK{cik:010d}", "https://data.sec.gov", f"/submissions/CIK{cik:010d}.json")


REF_SCHEMA = {"cik": pl.Int64, "entity_type": pl.Utf8, "sic": pl.Utf8, "sec_name": pl.Utf8, "sec_names": pl.Utf8}


def company_meta(ciks: Sequence[int], log=print) -> pl.DataFrame:
    rows = []
    for i, cik in enumerate(ciks):
        sub = submissions(int(cik))
        if sub is None:
            continue
        former = [f.get("name") for f in (sub.get("formerNames") or []) if f.get("name")]
        rows.append({"cik": int(cik), "entity_type": sub.get("entityType"), "sic": str(sub.get("sic") or ""), "sec_name": sub.get("name"),
                     "sec_names": "|".join([sub.get("name") or "", *former])})
        if (i + 1) % 250 == 0:
            log(f"  sec submissions {i + 1}/{len(ciks)}")
    return pl.DataFrame(rows, schema=REF_SCHEMA)


def company_reference(ciks: Sequence[int], log=print) -> pl.DataFrame:
    """Current SEC company metadata (entity type, SIC, name) kept at data/reference/edgar_company.parquet;
    only CIKs not yet saved are read. Reference data for classification, not a point-in-time series."""
    path = settings().raw_dir.parent / "reference" / "edgar_company.parquet"
    have = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=REF_SCHEMA)
    if "sec_names" not in have.columns:  # older file: re-read everything (submissions are cached per day)
        have = pl.DataFrame(schema=REF_SCHEMA)
    miss = sorted(set(int(c) for c in ciks) - set(have["cik"]))
    if miss:
        log(f"  company reference: reading {len(miss)} CIKs")
        have = pl.concat([have, company_meta(miss, log=log)]).unique("cik", keep="last")
        path.parent.mkdir(parents=True, exist_ok=True)
        have.write_parquet(path)
    return have.filter(pl.col("cik").is_in([int(c) for c in ciks]))


def _acceptance_to_utc(col: str) -> pl.Expr:
    # "2024-05-02T20:30:34.000Z" is UTC
    return (
        pl.col(col).str.slice(0, 19).str.to_datetime("%Y-%m-%dT%H:%M:%S", time_unit="us")
        .dt.replace_time_zone("UTC")
    )


def _filings_frame(cik: int, block: dict) -> pl.DataFrame:
    if not block.get("accessionNumber"):
        return pl.DataFrame()
    keys = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument", "isXBRL"]
    df = pl.DataFrame({k: block.get(k, [None] * len(block["accessionNumber"])) for k in keys}, strict=False)
    return df.with_columns(pl.lit(cik, pl.Int64).alias("cik"))


def ingest_filings(ciks: Sequence[int], log=print) -> int:
    frames = []
    for i, cik in enumerate(ciks):
        sub = submissions(int(cik))
        if sub is None:
            continue
        frames.append(_filings_frame(int(cik), sub["filings"]["recent"]))
        for extra in sub["filings"].get("files", []):
            if extra.get("filingTo", "9999") < str(settings().history_start.year - 1):
                continue
            older = _cached_json("sec_submissions", extra["name"].removesuffix(".json"), "https://data.sec.gov", f"/submissions/{extra['name']}")
            if older:
                frames.append(_filings_frame(int(cik), older))
        if (i + 1) % 250 == 0:
            log(f"  filings {i + 1}/{len(ciks)}")
    df = pl.concat([f for f in frames if not f.is_empty()], how="diagonal_relaxed")
    df = df.with_columns(
        known_ts=_acceptance_to_utc("acceptanceDateTime"),
        filing_date=pl.col("filingDate").str.to_date(strict=False),
        report_date=pl.col("reportDate").str.to_date(strict=False),
    )
    df = df.with_columns(
        # Fallback when acceptance time is missing: end of filing date, New York.
        known_ts=pl.coalesce(
            "known_ts",
            (pl.col("filing_date").cast(pl.Datetime("us")) + pl.duration(hours=23, minutes=59))
            .dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
        )
    ).with_columns(
        event_ts=pl.coalesce(
            pl.col("report_date").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
            pl.col("known_ts"),
        )
    ).select(
        "cik", pl.col("accessionNumber").alias("accession"), "form", pl.col("items").fill_null(""),
        "filing_date", "report_date", "event_ts", "known_ts", pl.col("primaryDocument").alias("primary_doc"),
        pl.col("isXBRL").cast(pl.Int8, strict=False).alias("is_xbrl"),
    ).filter(pl.col("filing_date") >= settings().history_start.replace(year=settings().history_start.year - 1))
    df, dropped = lake.drop_holdout(df)
    log(f"  filings rows {df.height} (dropped {dropped} inside holdout)")
    return lake.write(df, FILINGS, key=["cik", "accession"])


def ingest_facts(ciks: Sequence[int], log=print) -> int:
    acc = lake.scan(FILINGS, allow_forward=True).select("accession", pl.col("known_ts").alias("acc_known_ts")).collect()
    frames = []
    for i, cik in enumerate(ciks):
        data = _cached_json("sec_companyfacts", f"CIK{int(cik):010d}", "https://data.sec.gov", f"/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
        if not data:
            continue
        rows = []
        for tax, concepts in CONCEPTS.items():
            facts = data.get("facts", {}).get(tax, {})
            for c in concepts:
                for unit, vals in facts.get(c, {}).get("units", {}).items():
                    for v in vals:
                        rows.append({
                            "cik": int(cik), "taxonomy": tax, "concept": c, "unit": unit,
                            "start": v.get("start"), "end": v.get("end"), "val": float(v["val"]),
                            "accession": v.get("accn"), "fy": v.get("fy"), "fp": v.get("fp"),
                            "form": v.get("form"), "filed": v.get("filed"), "frame": v.get("frame"),
                        })
        if rows:
            frames.append(pl.DataFrame(rows, infer_schema_length=None))
        if (i + 1) % 100 == 0:
            log(f"  facts {i + 1}/{len(ciks)}")
    if not frames:
        log("  no XBRL facts in this batch")
        return 0
    df = pl.concat(frames, how="diagonal_relaxed").with_columns(
        pl.col("start").str.to_date(strict=False), pl.col("end").str.to_date(strict=False),
        pl.col("filed").str.to_date(strict=False), pl.col("fy").cast(pl.Int32, strict=False),
    )
    df = df.join(acc, on="accession", how="left").with_columns(
        known_ts=pl.coalesce(
            "acc_known_ts",
            (pl.col("filed").cast(pl.Datetime("us")) + pl.duration(hours=23, minutes=59))
            .dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
        ),
        event_ts=pl.col("end").cast(pl.Datetime("us")).dt.replace_time_zone("America/New_York").dt.convert_time_zone("UTC"),
    ).drop("acc_known_ts").filter(pl.col("end") >= settings().history_start.replace(year=settings().history_start.year - 2))
    df, dropped = lake.drop_holdout(df)
    log(f"  fact rows {df.height} (dropped {dropped} inside holdout)")
    return lake.write(df, FACTS, key=["cik", "concept", "unit", "start", "end", "accession"])
