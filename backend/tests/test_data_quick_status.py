"""Fast data-page preview must not read/aggregate entire historical bars."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import polars as pl

from app.api.data import quick_status, _partition_date_stats

def test_quick_status_uses_local_metadata_only():
    with TemporaryDirectory() as home:
        folder = Path(home)
        for name in ("kline_daily", "kline_daily_enriched", "kline_index_daily",
                     "kline_index_enriched", "kline_etf_daily", "kline_etf_enriched", "kline_minute"):
            for day in ("2026-11-02", "2026-11-03"):
                (folder / name / f"date={day}").mkdir(parents=True)
        pl.DataFrame({"close": [0.424], "symbol": ["515880.SH"]}).write_parquet(
            folder / "kline_daily_enriched/date=2026-11-03/part.parquet"
        )

        class Repo:
            store = SimpleNamespace(data_dir=folder)
            def execute_one(self, sql, params=None):
                assert "FROM kline_daily" not in sql
                if "FROM instruments_index" in sql:
                    return (10, 10, 10)
                assert "FROM instruments" in sql
                return (5, 5, "2026-11-03", 5)
            def get_etf_instruments(self):
                return pl.DataFrame({"symbol": []})
            def execute_all(self, sql):
                raise AssertionError("A fast preview must never scan full historical view")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            repo=Repo(), indicators_ready=True,
        )))
        result = quick_status(request)
        assert result["partial"] is True
        assert result["daily"]["trading_days"] == 2
        assert result["enriched"]["fields"] == 2
        assert result["index_daily"]["symbols_covered"] == 10
        assert result["etf_daily"]["trading_days"] == 2
        assert result["minute"]["latest_date"] == "2026-11-03"
        assert result["storage"] is None
        assert _partition_date_stats(folder / "missing") is None
