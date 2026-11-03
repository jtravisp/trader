"""P1 survivorship check (D3).

Question: can we rebuild a past universe from Alpaca alone? Test set: notable large/mid caps that
left the market 2016-2025 (acquired, failed, taken private). For each: is it in Alpaca's active or
inactive asset lists (i.e. can we *enumerate* it)? Does Alpaca still serve its bars by symbol?
Renames are checked separately: does the new ticker carry the old ticker's history?
"""

from __future__ import annotations

from datetime import date

import polars as pl

from trader.config import settings
from trader.data.bars import _fetch
from trader.data.http import RawArchive
from trader.universe.build import active_assets, inactive_assets

# (ticker, company, year it stopped trading, how)
DELISTED = [
    ("LNKD", "LinkedIn", 2016, "acquired MSFT"), ("WFM", "Whole Foods", 2017, "acquired AMZN"),
    ("MON", "Monsanto", 2018, "acquired Bayer"), ("TWX", "Time Warner", 2018, "acquired T"),
    ("AET", "Aetna", 2018, "acquired CVS"), ("ESRX", "Express Scripts", 2018, "acquired CI"),
    ("CA", "CA Inc", 2018, "acquired AVGO"), ("COL", "Rockwell Collins", 2018, "acquired UTX"),
    ("ANDV", "Andeavor", 2018, "acquired MPC"), ("SNI", "Scripps Networks", 2018, "acquired DISCA"),
    ("RHT", "Red Hat", 2019, "acquired IBM"), ("APC", "Anadarko", 2019, "acquired OXY"),
    ("CELG", "Celgene", 2019, "acquired BMY"), ("SCG", "SCANA", 2019, "acquired D"),
    ("STI", "SunTrust", 2019, "merged into TFC"), ("AGN", "Allergan", 2020, "acquired ABBV"),
    ("ETFC", "E*TRADE", 2020, "acquired MS"), ("NBL", "Noble Energy", 2020, "acquired CVX"),
    ("TIF", "Tiffany", 2021, "acquired LVMH"), ("CXO", "Concho Resources", 2021, "acquired COP"),
    ("MXIM", "Maxim Integrated", 2021, "acquired ADI"), ("ALXN", "Alexion", 2021, "acquired AZN"),
    ("FLIR", "FLIR Systems", 2021, "acquired TDY"), ("KSU", "Kansas City Southern", 2021, "acquired CP"),
    ("XLNX", "Xilinx", 2022, "acquired AMD"), ("CERN", "Cerner", 2022, "acquired ORCL"),
    ("CTXS", "Citrix", 2022, "taken private"), ("NLSN", "Nielsen", 2022, "taken private"),
    ("TWTR", "Twitter", 2022, "taken private"), ("ABMD", "Abiomed", 2022, "acquired JNJ"),
    ("SIVB", "SVB Financial", 2023, "bank failure"), ("FRC", "First Republic", 2023, "bank failure"),
    ("SBNY", "Signature Bank", 2023, "bank failure"), ("BBBY", "Bed Bath & Beyond", 2023, "bankruptcy"),
    ("ATVI", "Activision Blizzard", 2023, "acquired MSFT"), ("VMW", "VMware", 2023, "acquired AVGO"),
    ("SGEN", "Seagen", 2023, "acquired PFE"), ("HZNP", "Horizon Therapeutics", 2023, "acquired AMGN"),
    ("SPLK", "Splunk", 2024, "acquired CSCO"), ("PXD", "Pioneer Natural Resources", 2024, "acquired XOM"),
    ("MRO", "Marathon Oil", 2024, "acquired COP"), ("CTLT", "Catalent", 2024, "taken private"),
    ("HES", "Hess", 2025, "acquired CVX"), ("JNPR", "Juniper Networks", 2025, "acquired HPE"),
    ("ANSS", "Ansys", 2025, "acquired SNPS"), ("DFS", "Discover Financial", 2025, "acquired COF"),
    ("WBA", "Walgreens Boots Alliance", 2025, "taken private"), ("X", "U.S. Steel", 2025, "acquired Nippon Steel"),
]
# (old ticker, new ticker, year of change)
RENAMES = [("FB", "META", 2022), ("SQ", "XYZ", 2025), ("UTX", "RTX", 2020), ("DISCA", "WBD", 2022)]


def _has_bars(sym: str, d0: date, d1: date, archive: RawArchive) -> int:
    df = _fetch([sym], "1Day", d0, d1, "raw", archive)
    return 0 if df.is_empty() else df.height


def run(log=print) -> pl.DataFrame:
    act = set(active_assets()["symbol"].to_list())
    ina = set(inactive_assets()["symbol"].to_list())
    arch = RawArchive("alpaca_bars_daily", "survivorship_check")
    rows = []
    for t, name, yr, how in DELISTED:
        rows.append({
            "ticker": t, "company": name, "left": yr, "how": how,
            "in_active": t in act, "in_inactive": t in ina,
            "bars_2017q1": _has_bars(t, date(2017, 1, 1), date(2017, 4, 1), arch),
        })
    df = pl.DataFrame(rows)
    enumerable = df.filter(pl.col("in_active") | pl.col("in_inactive")).height
    with_bars = df.filter(pl.col("bars_2017q1") > 0).height
    log(f"delisted test set: {df.height} names; enumerable from Alpaca asset lists: {enumerable}; bars still served: {with_bars}")
    ren = []
    for old, new, yr in RENAMES:
        ren.append({
            "old": old, "new": new, "year": yr,
            "old_bars_2017q1": _has_bars(old, date(2017, 1, 1), date(2017, 4, 1), arch),
            "new_bars_2017q1": _has_bars(new, date(2017, 1, 1), date(2017, 4, 1), arch),
        })
    rdf = pl.DataFrame(ren)
    out = settings().quality_dir / "survivorship.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Survivorship check (P1)", "",
        f"Delisted test set: {df.height} names. Enumerable from Alpaca active+inactive asset lists: "
        f"**{enumerable}/{df.height}**. Daily bars still served by symbol for 2017Q1: **{with_bars}/{df.height}**.", "",
        "| ticker | company | left | how | in active | in inactive | 2017Q1 bars |", "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['ticker']} | {r['company']} | {r['left']} | {r['how']} | {r['in_active']} | {r['in_inactive']} | {r['bars_2017q1']} |" for r in rows]
    lines += ["", "## Ticker renames", "", "| old | new | year | old-ticker 2017Q1 bars | new-ticker 2017Q1 bars |", "|---|---|---|---|---|"]
    lines += [f"| {r['old']} | {r['new']} | {r['year']} | {r['old_bars_2017q1']} | {r['new_bars_2017q1']} |" for r in ren]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"renames:\n{rdf}")
    log(f"wrote {out}")
    return df
