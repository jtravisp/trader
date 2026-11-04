"""S05: LightGBM ranker, walk-forward (declared in docs/STRATEGIES.md before any fit).

Pure: everything is computed from the Panel passed in (rows 0..T-1). The model used for year Y
is trained only on decision rows whose label window ends, plus an embargo, before Y's first
session. So truncating the panel at any t inside Y leaves Y's model, and every target row <= t,
unchanged; the engine's truncation leak check verifies this.
"""

from __future__ import annotations

import numpy as np

from trader.backtest.panel import Panel
from trader.features import fundamental, price
from trader.signals.base import Strategy, liquid_universe, only_on, schedule, top_k_equal_weight

HORIZON = 5  # label: open t+1 -> open t+1+HORIZON
EMBARGO = 5
FIRST_TEST_YEAR = 2019
REGIME = {"spy_trend200", "spy_vol20", "vix", "curve_10y2y", "curve_10y3m", "credit_baa_aaa", "rate_10y"}
FUND = {"log_mcap", "earnings_yield", "book_to_market", "sales_to_price", "roa", "accruals", "leverage",
        "revenue_growth", "eps_growth", "filing_age"}
FRED = {"vix", "curve_10y2y", "curve_10y3m", "credit_baa_aaa", "rate_10y"}
FIXED = {"objective": "regression", "learning_rate": 0.03, "min_data_in_leaf": 500, "feature_fraction": 0.8,
         "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "seed": 0, "deterministic": True,
         "num_threads": 4, "verbose": -1}

_CACHE: dict = {}


def feature_set(panel: Panel, which: str) -> dict[str, np.ndarray]:
    f = {k: v for k, v in price.build(panel).items() if k not in REGIME}
    if which == "all":
        f |= {k: v for k, v in price.build(panel).items() if k in REGIME}
    if which in ("price+fund", "all"):
        f |= {k: v for k, v in fundamental.build(panel).items() if k in FUND}
    if which == "all":
        f |= {k: v for k, v in fundamental.build(panel).items() if k in FRED}
    return f


def label(panel: Panel) -> np.ndarray:
    """SPY-relative open(t+1) -> open(t+1+H) return; NaN where not fully observed in the panel."""
    o = panel.open
    out = np.full(o.shape, np.nan)
    a, b = 1, 1 + HORIZON
    if panel.T > b:
        with np.errstate(invalid="ignore", divide="ignore"):
            r = o[b:] / o[a : panel.T - HORIZON] - 1
        out[: panel.T - b] = r - r[:, [panel.col("SPY")]]
    return out


class LgbmRanker(Strategy):
    id, family = "S05", "S05"
    needs_fields = True

    def _matrix(self, panel: Panel):
        which = self.params.get("features", "price")
        n = int(self.params.get("n", 500))
        feats = feature_set(panel, which)
        uni = liquid_universe(panel, n=n)
        names = sorted(feats)
        X = np.stack([price.cs_rank(feats[k], uni) if k not in REGIME else feats[k] for k in names], axis=-1)
        y = price.cs_rank(label(panel), uni)
        return names, X, y, uni

    def targets(self, panel: Panel) -> np.ndarray:
        import lightgbm as lgb

        key = (tuple(sorted(self.params.items())), id(panel.close), panel.T)
        if key in _CACHE:
            return _CACHE[key].copy()
        names, X, y, uni = self._matrix(panel)
        dec = schedule(panel, "weekly")
        years = panel.dates.astype("datetime64[Y]").astype(int) + 1970
        pred = np.full(uni.shape, np.nan)
        self.importance = {}
        params = {**FIXED, "num_leaves": int(self.params.get("num_leaves", 15))}
        rounds = int(self.params.get("n_estimators", 300))
        for yr in range(FIRST_TEST_YEAR, int(years[-1]) + 1):
            test = np.where(years == yr)[0]
            if not len(test):
                continue
            start = int(test[0])
            # training decision rows: label window (t+1+H) plus embargo strictly before the test year
            tr_rows = np.where(dec & (np.arange(panel.T) + 1 + HORIZON + EMBARGO < start))[0]
            m = uni[tr_rows] & np.isfinite(y[tr_rows])
            Xtr, ytr = X[tr_rows][m], y[tr_rows][m]
            if len(ytr) < 5000:
                continue
            model = lgb.train(params, lgb.Dataset(Xtr, ytr, feature_name=names, free_raw_data=True), num_boost_round=rounds)
            self.importance[yr] = dict(zip(names, model.feature_importance("gain").tolist(), strict=True))
            rows = test[dec[test]]
            for t in rows:
                ok = uni[t]
                if ok.any():
                    pred[t, ok] = model.predict(X[t, ok])
        k = int(self.params.get("k", int(self.params.get("n", 500)) // 10))
        w = only_on(dec, top_k_equal_weight(pred, uni & np.isfinite(pred), k))
        _CACHE[key] = w
        return w.copy()
