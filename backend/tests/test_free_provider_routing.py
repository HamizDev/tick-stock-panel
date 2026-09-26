"""Free-provider routing and realtime safety tests."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import polars as pl

from app.services import index_sync, preferences, quote_service
from app.tickflow.capabilities import CapabilitySet
from app.tickflow.repository import DataStore, KlineRepository

ETF = "159915.SZ"


def _etf_bar(day: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [ETF],
            "date": [day],
            "open": [2.0],
            "high": [2.1],
            "low": [1.9],
            "close": [2.05],
            "volume": [1000.0],
            "amount": [205000.0],
        }
    )


def test_etf_daily_custom_provider_does_not_require_tickflow_capability(tmp_path, monkeypatch):
    repo = KlineRepository(DataStore(tmp_path))
    calls: list[tuple[list[str], str]] = []

    class _Provider:
        config = SimpleNamespace(datasets={"daily": {}})

        def get_daily(
            self,
            symbols,
            start_time,
            end_time,
            asset_type="stock",
            on_chunk_done=None,
        ):
            calls.append((list(symbols), asset_type))
            if on_chunk_done:
                on_chunk_done(1, 1)
            return _etf_bar(date(2026, 9, 24))

    monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: "akshare")
    from app.data_providers import custom as custom_sources

    monkeypatch.setattr(
        custom_sources,
        "provider_has_dataset",
        lambda name, dataset: name == "akshare" and dataset == "daily",
    )
    monkeypatch.setattr(custom_sources, "get_provider", lambda name: _Provider())

    progress: list[tuple[int, int]] = []
    written = index_sync.sync_and_persist_etf_daily(
        repo,
        CapabilitySet({}),
        start_date=datetime(2026, 9, 24),
        end_date=datetime(2026, 9, 24, 15, 0),
        symbols_override=[ETF],
        on_chunk_done=lambda cur, total: progress.append((cur, total)),
    )

    assert written == 1
    assert calls == [([ETF], "etf")]
    assert progress == [(1, 1)]
    stored = pl.read_parquet(
        tmp_path / "kline_etf_daily" / "date=2026-09-24" / "part.parquet"
    )
    assert stored["symbol"].to_list() == [ETF]


def test_custom_realtime_provider_minimum_interval_is_respected(monkeypatch):
    monkeypatch.setattr(preferences, "get_realtime_data_provider", lambda: "akshare")
    from app.data_providers import custom as custom_sources

    provider = SimpleNamespace(min_realtime_interval=15.0)
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda *_: True)
    monkeypatch.setattr(custom_sources, "get_provider", lambda *_: provider)

    assert quote_service.QuoteService._tier_min_interval() == 15.0

    monkeypatch.setattr(preferences, "load", lambda: {})
    service = quote_service.QuoteService()
    service._interval = 6.0  # e.g. service already running before provider switch
    assert service._effective_interval() == 15.0


def test_unverifiable_realtime_snapshot_fails_closed_for_final(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {})
    monkeypatch.setattr(preferences, "get_realtime_data_provider", lambda: "akshare")
    monkeypatch.setattr(quote_service, "_persist_last_fetch", lambda _ts: None)
    from app.data_providers import custom as custom_sources

    provider = SimpleNamespace(
        snapshot_timestamp_reliable=False,
        get_realtime=lambda: [
            {
                "symbol": "600000.SH",
                "last_price": 10.1,
                "prev_close": 10.0,
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "volume": 1000.0,
                "amount": 1010000.0,
                "change_pct": 0.01,
                "change_amount": 0.1,
                "amplitude": 0.03,
                "turnover_rate": 0.01,
                "timestamp": None,
                "session": None,
            }
        ],
    )
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda *_: True)
    monkeypatch.setattr(custom_sources, "get_provider", lambda *_: provider)

    service = quote_service.QuoteService()
    ok = service._fetch_quotes(final=True, final_boundary_ms=1_000)

    assert ok is True
    assert service._last_final_unverifiable is True
    assert service._last_final_confirmed is False


def test_poll_loop_stops_final_retry_for_unverifiable_provider(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {})
    service = quote_service.QuoteService()
    service._running = True
    service._enabled = True

    monkeypatch.setattr(service, "_market_phase", lambda: "close_final")
    monkeypatch.setattr(service, "_should_fetch_for_phase", lambda _phase: True)
    monkeypatch.setattr(service, "_final_boundary_ms", lambda _phase: 1_000)

    def fake_fetch(**_kwargs):
        service._last_final_confirmed = False
        service._last_final_unverifiable = True
        service._running = False
        return True

    monkeypatch.setattr(service, "_fetch_quotes", fake_fetch)
    service._poll_loop()

    key = service._final_sync_key("close_final")
    assert key in service._final_sync_done
    assert service._final_sync_failed[key] == "unverifiable_snapshot"
