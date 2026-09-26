"""AKShare phase-1 provider normalization tests (no network)."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.plugins.akshare import provider as ak_provider
from app.services import preferences


def test_realtime_normalizes_stock_etf_and_percent_units(monkeypatch):
    fake = SimpleNamespace(
        stock_zh_a_spot_em=lambda: pl.DataFrame(
            {
                "代码": ["600000", "920001"],
                "名称": ["浦发银行", "北交样例"],
                "最新价": [10.2, 8.5],
                "昨收": [10.0, 8.0],
                "今开": [10.0, 8.1],
                "最高": [10.3, 8.6],
                "最低": [9.9, 8.0],
                "成交量": [12345.0, 2345.0],
                "成交额": [12500000.0, 1990000.0],
                "涨跌幅": [2.0, 6.25],
                "涨跌额": [0.2, 0.5],
                "振幅": [4.0, 7.5],
                "换手率": [1.5, 2.5],
            }
        ),
        fund_etf_spot_em=lambda: pl.DataFrame(
            {
                "代码": ["159915"],
                "名称": ["创业板ETF"],
                "最新价": [2.04],
                "昨收": [2.0],
                "开盘价": [2.01],
                "最高价": [2.05],
                "最低价": [1.99],
                "成交量": [9876.0],
                "成交额": [20000000.0],
                "涨跌幅": [2.0],
                "涨跌额": [0.04],
                "振幅": [3.0],
                "换手率": [4.0],
            }
        ),
    )
    monkeypatch.setattr(ak_provider, "_ak", lambda: fake)
    monkeypatch.setattr(preferences, "get_realtime_pull_stock", lambda: True)
    monkeypatch.setattr(preferences, "get_realtime_pull_etf", lambda: True)

    rows = ak_provider.AkShareProvider().get_realtime()
    by_symbol = {row["symbol"]: row for row in rows}

    assert set(by_symbol) == {"600000.SH", "920001.BJ", "159915.SZ"}
    assert by_symbol["600000.SH"]["change_pct"] == pytest.approx(0.02)
    assert by_symbol["600000.SH"]["turnover_rate"] == pytest.approx(0.015)
    assert by_symbol["159915.SZ"]["amplitude"] == pytest.approx(0.03)
    assert by_symbol["159915.SZ"]["open"] == pytest.approx(2.01)
    assert all(row["timestamp"] is None for row in rows)


def test_daily_routes_stock_and_etf_to_unadjusted_akshare_endpoints(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def stock_hist(**kwargs):
        calls.append(("stock", kwargs))
        return pl.DataFrame(
            {
                "日期": ["2026-09-24"],
                "开盘": [10.0],
                "收盘": [10.5],
                "最高": [10.8],
                "最低": [9.9],
                "成交量": [1000.0],
                "成交额": [1050000.0],
            }
        )

    def etf_hist(**kwargs):
        calls.append(("etf", kwargs))
        return pl.DataFrame(
            {
                "日期": ["2026-09-24"],
                "开盘": [2.0],
                "收盘": [2.1],
                "最高": [2.12],
                "最低": [1.98],
                "成交量": [2000.0],
                "成交额": [420000.0],
            }
        )

    fake = SimpleNamespace(stock_zh_a_hist=stock_hist, fund_etf_hist_em=etf_hist)
    monkeypatch.setattr(ak_provider, "_ak", lambda: fake)

    provider = ak_provider.AkShareProvider()
    stock = provider.get_daily(
        ["600000.SH"],
        datetime(2026, 9, 1),
        datetime(2026, 9, 24),
        asset_type="stock",
    )
    etf = provider.get_daily(
        ["159915.SZ"],
        datetime(2026, 9, 1),
        datetime(2026, 9, 24),
        asset_type="etf",
    )

    assert stock["symbol"].to_list() == ["600000.SH"]
    assert stock["date"].to_list() == [date(2026, 9, 24)]
    assert etf["symbol"].to_list() == ["159915.SZ"]
    assert etf["date"].to_list() == [date(2026, 9, 24)]
    assert calls == [
        (
            "stock",
            {
                "symbol": "600000",
                "period": "daily",
                "start_date": "20260901",
                "end_date": "20260924",
                "adjust": "",
            },
        ),
        (
            "etf",
            {
                "symbol": "159915",
                "period": "daily",
                "start_date": "20260901",
                "end_date": "20260924",
                "adjust": "",
            },
        ),
    ]
