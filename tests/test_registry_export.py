import numpy as np
import polars as pl

from trader.research import registry


def test_record_rewrites_sorted_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DB", tmp_path / "registry.db")
    monkeypatch.setattr(registry, "RETURNS", tmp_path / "returns")
    dates = np.array(["2020-01-02", "2020-01-03"], dtype="datetime64[D]")
    ids = [
        registry.record(family="F", strategy_id=f"S{i}", params={"k": i}, cost_mult=1.0, delay=0,
                        data_start="2020-01-02", data_end="2020-01-03", leak_check="ok", status="ok",
                        metrics={"sharpe": 0.1 * i}, dates=dates, net=np.array([0.01, -0.01]), sharpe_daily=0.0)
        for i in range(3)
    ]
    df = pl.read_csv(tmp_path / "trials.csv")
    assert df.height == 3
    assert df["trial_id"].to_list() == sorted(ids)
    assert set(df["holdout_touched"].to_list()) == {0}
