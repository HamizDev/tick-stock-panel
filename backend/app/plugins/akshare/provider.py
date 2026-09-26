"""AKShare free market-data provider.

Phase 1 scope:
- A-share / ETF unadjusted daily bars
- A-share / ETF full-market realtime snapshots

The provider is optional and lazily imports AKShare so the core application keeps
working when the plugin dependency is not installed.

AKShare wraps public web sources. Those sources can throttle or change schema, so
this provider is intentionally not selected by default and keeps failures isolated.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl

from app.data_providers.normalizer import normalize_daily

logger = logging.getLogger(__name__)

_DAILY_BATCH_SIZE = 20
_DAILY_RETRY_ATTEMPTS = 2


def availability() -> tuple[bool, str]:
    """Return plugin availability without importing the heavy dependency."""
    if importlib.util.find_spec("akshare") is None:
        return False, "未安装 akshare"
    return True, "ok"


def _ak():
    return importlib.import_module("akshare")


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _pct(value) -> float | None:
    """AKShare percent value (1.23 = 1.23%) -> internal decimal (0.0123)."""
    number = _number(value)
    return None if number is None else number / 100.0


def _records(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, pl.DataFrame):
        return data.to_dicts()
    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
            return [dict(row) for row in rows]
        except TypeError:
            pass
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    return []


def _stock_symbol(code: str) -> str:
    code = str(code or "").strip().lower().removeprefix("sh").removeprefix("sz").removeprefix("bj")
    if code.startswith(("4", "8", "92")):
        exchange = "BJ"
    elif code.startswith(("6", "68")):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{code}.{exchange}" if code else ""


def _etf_symbol(code: str) -> str:
    code = str(code or "").strip().lower().removeprefix("sh").removeprefix("sz")
    exchange = "SH" if code.startswith("5") else "SZ"
    return f"{code}.{exchange}" if code else ""


def _daily_from_rows(rows: list[dict], symbol: str) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    mapped = []
    for row in rows:
        mapped.append(
            {
                "symbol": symbol,
                "date": row.get("日期") or row.get("date"),
                "open": row.get("开盘") if "开盘" in row else row.get("open"),
                "high": row.get("最高") if "最高" in row else row.get("high"),
                "low": row.get("最低") if "最低" in row else row.get("low"),
                "close": row.get("收盘") if "收盘" in row else row.get("close"),
                "volume": row.get("成交量") if "成交量" in row else row.get("volume"),
                "amount": row.get("成交额") if "成交额" in row else row.get("amount"),
            }
        )
    return normalize_daily(mapped, default_symbol=symbol, source="akshare")


def _snapshot_record(row: dict, *, asset_type: str) -> dict | None:
    code = str(row.get("代码") or "").strip()
    symbol = _etf_symbol(code) if asset_type == "etf" else _stock_symbol(code)
    if not symbol:
        return None

    if asset_type == "etf":
        open_value = row.get("开盘价")
        high_value = row.get("最高价")
        low_value = row.get("最低价")
    else:
        open_value = row.get("今开")
        high_value = row.get("最高")
        low_value = row.get("最低")

    last_price = _number(row.get("最新价"))
    prev_close = _number(row.get("昨收"))
    change_amount = _number(row.get("涨跌额"))
    if change_amount is None and last_price is not None and prev_close is not None:
        change_amount = last_price - prev_close

    change_pct = _pct(row.get("涨跌幅"))
    if change_pct is None and change_amount is not None and prev_close not in (None, 0):
        change_pct = change_amount / prev_close

    return {
        "symbol": symbol,
        "name": row.get("名称"),
        "last_price": last_price,
        "prev_close": prev_close,
        "open": _number(open_value),
        "high": _number(high_value),
        "low": _number(low_value),
        # Eastmoney stock/ETF spot interfaces expose volume in hands.
        "volume": _number(row.get("成交量")),
        "amount": _number(row.get("成交额")),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "amplitude": _pct(row.get("振幅")),
        "turnover_rate": _pct(row.get("换手率")),
        # stock_zh_a_spot_em does not expose a trustworthy quote timestamp.
        # Keep it null so final-session persistence fails closed.
        "timestamp": None,
        "session": None,
    }


class AkShareProvider:
    name = "akshare"
    builtin = True

    # Public Eastmoney endpoints are not suitable for 1-second polling.
    min_realtime_interval = 15.0
    # QuoteService uses this to stop final-session retries and let the post-close
    # official daily pipeline finalize the partition instead of trusting local time.
    snapshot_timestamp_reliable = False

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            display_name="AKShare",
            datasets={"daily": {}, "realtime": {}},
        )

    def close(self) -> None:
        return None

    def trading_days(self) -> set[date]:
        """Return the A-share trading calendar when AKShare's Sina calendar is current.

        The oracle consumer checks the calendar's maximum date before using a negative
        verdict, so a stale upstream calendar degrades to "unknown" instead of marking
        future weekdays as holidays.
        """
        try:
            rows = _records(_ak().tool_trade_date_hist_sina())
        except Exception as exc:  # noqa: BLE001
            logger.warning("AKShare 交易日历获取失败: %s", exc)
            return set()

        days: set[date] = set()
        for row in rows:
            value = row.get("trade_date")
            if isinstance(value, datetime):
                days.add(value.date())
            elif isinstance(value, date):
                days.add(value)
            elif value:
                try:
                    days.add(datetime.fromisoformat(str(value).split(" ", 1)[0]).date())
                except ValueError:
                    continue
        return days

    def _fetch_one_daily(
        self,
        symbol: str,
        *,
        start_time: datetime,
        end_time: datetime,
        asset_type: str,
    ) -> pl.DataFrame:
        ak = _ak()
        code = symbol.split(".", 1)[0]
        start = start_time.strftime("%Y%m%d")
        end = end_time.strftime("%Y%m%d")
        last_error: Exception | None = None
        for attempt in range(1, _DAILY_RETRY_ATTEMPTS + 1):
            try:
                if asset_type == "etf":
                    raw = ak.fund_etf_hist_em(
                        symbol=code,
                        period="daily",
                        start_date=start,
                        end_date=end,
                        adjust="",
                    )
                else:
                    raw = ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start,
                        end_date=end,
                        adjust="",
                    )
                return _daily_from_rows(_records(raw), symbol)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "AKShare %s 日K失败 %s (attempt %d/%d): %s",
                    asset_type,
                    symbol,
                    attempt,
                    _DAILY_RETRY_ATTEMPTS,
                    exc,
                )
                if attempt < _DAILY_RETRY_ATTEMPTS:
                    time.sleep(0.5 * attempt)
        logger.warning("AKShare %s 日K最终失败 %s: %s", asset_type, symbol, last_error)
        return pl.DataFrame()

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        if not symbols:
            return
        if asset_type not in {"stock", "etf"}:
            logger.warning("AKShare daily 暂不支持 asset_type=%s", asset_type)
            return

        end = end_time or datetime.now()
        start = start_time or (end - timedelta(days=365))
        total = (len(symbols) + _DAILY_BATCH_SIZE - 1) // _DAILY_BATCH_SIZE

        for batch_index, offset in enumerate(range(0, len(symbols), _DAILY_BATCH_SIZE), start=1):
            batch = symbols[offset : offset + _DAILY_BATCH_SIZE]
            frames: list[pl.DataFrame] = []
            for symbol in batch:
                frame = self._fetch_one_daily(
                    symbol,
                    start_time=start,
                    end_time=end,
                    asset_type=asset_type,
                )
                if not frame.is_empty():
                    frames.append(frame)
            if on_chunk_done:
                on_chunk_done(batch_index, total)
            if frames:
                yield pl.concat(frames, how="diagonal_relaxed")

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        frames = list(
            self.iter_daily(
                symbols,
                start_time=start_time,
                end_time=end_time,
                asset_type=asset_type,
                on_chunk_done=on_chunk_done,
            )
        )
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_realtime(self) -> list[dict]:
        """Return A-share + ETF snapshots according to the user's realtime scope."""
        from app.services import preferences

        ak = _ak()
        records: list[dict] = []

        if preferences.get_realtime_pull_stock():
            try:
                for row in _records(ak.stock_zh_a_spot_em()):
                    rec = _snapshot_record(row, asset_type="stock")
                    if rec is not None:
                        records.append(rec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AKShare A股实时行情失败: %s", exc)

        if preferences.get_realtime_pull_etf():
            try:
                for row in _records(ak.fund_etf_spot_em()):
                    rec = _snapshot_record(row, asset_type="etf")
                    if rec is not None:
                        records.append(rec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AKShare ETF实时行情失败: %s", exc)

        # Guard against accidental duplicate symbols if an upstream feed changes scope.
        dedup: dict[str, dict] = {}
        for row in records:
            symbol = row.get("symbol")
            if symbol:
                dedup[str(symbol)] = row
        return list(dedup.values())
