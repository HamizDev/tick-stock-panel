"""基金中心业务层: 字段归一 + TTL 缓存 + 自选持久化。

字段归一口径 (与 docs/plugin-development.md 一致):
- 百分数原值 (如 1.74) → 小数制 (0.0174)
- volume: 股/份 → 手 (/100)
- 时间: 毫秒戳 → 北京时间日期 (date, naive)
- 缺字段 → None, 不伪造
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BEIJING = timezone(timedelta(hours=8))

# fuyao asset_type → 本模块资产种类 (展示用, 不混入核心 AssetType)
ASSET_KIND_LABEL = {
    "fund-etf": "ETF",
    "fund-lof": "LOF",
    "fund-otc": "场外基金",
    "fund-reits": "REITs",
}


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pct_to_decimal(v) -> float | None:
    """百分数原值 → 小数制。None 透传, 不做启发式。"""
    f = _to_float(v)
    return f / 100.0 if f is not None else None


def _beijing_date(ms) -> str | None:
    """毫秒戳 → 北京时间日期字符串 (YYYY-MM-DD)。"""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_BEIJING).date().isoformat()


def map_quote(row: dict, name: str | None = None) -> dict | None:
    """场内基金快照行 → 标准 quote。"""
    symbol = row.get("thscode")
    if not symbol:
        return None
    last = _to_float(row.get("last_price"))
    prev = _to_float(row.get("prev_price"))
    change_pct = _pct_to_decimal(row.get("price_change_ratio_pct"))
    change_amount = _to_float(row.get("price_change"))
    if change_amount is None and last is not None and prev is not None:
        change_amount = last - prev
    if change_pct is None and change_amount is not None and prev not in (None, 0):
        change_pct = change_amount / prev
    volume = _to_float(row.get("volume"))
    return {
        "thscode": symbol,
        "symbol": symbol,
        "name": name,
        "last_price": last,
        "prev_close": prev,
        "open": _to_float(row.get("open_price")),
        "high": _to_float(row.get("high_price")),
        "low": _to_float(row.get("low_price")),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "volume": math.floor(volume / 100.0) if volume is not None else None,
        "amount": _to_float(row.get("turnover")),
        "amplitude": _pct_to_decimal(row.get("price_amplitude_ratio_pct")),
        "turnover_rate": _pct_to_decimal(row.get("turnover_ratio_pct")),
        "timestamp": row.get("timestamp"),
    }


def map_kline(rows: list[dict]) -> dict:
    """场内基金历史日线 → 标准 K 线 (上游为前复权, 如实标注)。"""
    bars = []
    for r in rows:
        d = _beijing_date(r.get("date_ms") or r.get("date"))
        if not d:
            continue
        vol = _to_float(r.get("volume"))
        bars.append(
            {
                "date": d,
                "open": _to_float(r.get("open_price")),
                "high": _to_float(r.get("high_price")),
                "low": _to_float(r.get("low_price")),
                "close": _to_float(r.get("close_price")),
                "volume": math.floor(vol / 100.0) if vol is not None else None,
                "amount": _to_float(r.get("turnover")),
            }
        )
    bars.sort(key=lambda b: b["date"])
    return {"adjusted": "forward", "bars": bars}


def map_nav(rows: list[dict]) -> list[dict]:
    """基金净值序列 → [{nav_date, unit_nav, adj_nav}] (升序)。"""
    out = []
    for r in rows:
        d = _beijing_date(r.get("nav_date"))
        if d is None:
            continue
        out.append(
            {
                "nav_date": d,
                "unit_nav": _to_float(r.get("unit_nav")),
                "adj_nav": _to_float(r.get("adj_nav")),
            }
        )
    out.sort(key=lambda x: x["nav_date"])
    return out


def map_profile(row: dict) -> dict:
    """基金资料行 → 标准资料 (字段缺失置 None)。"""
    return {
        "thscode": row.get("thscode"),
        "ticker": row.get("ticker"),
        "fund_name": row.get("fund_name"),
        "estab_date": _beijing_date(row.get("estab_date")),
        "mgmt_name": row.get("mgmt_name"),
        "manager_name": row.get("manager_name"),
        "fund_type": row.get("fund_type"),
        "benchmark": row.get("benchmark"),
    }


def map_search(rows: list[dict]) -> list[dict]:
    """检索结果 → [{thscode, ticker, name, asset_type, kind_label}]。"""
    out = []
    for r in rows:
        thscode = r.get("thscode")
        if not thscode:
            continue
        at = r.get("asset_type") or ""
        out.append(
            {
                "thscode": thscode,
                "ticker": r.get("ticker"),
                "name": r.get("name") or r.get("fund_name"),
                "asset_type": at,
                "kind_label": ASSET_KIND_LABEL.get(at, at),
            }
        )
    return out


class TTLCache:
    """线程安全 TTL 缓存 (key → (expire_ts, value))。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            exp, val = hit
            if exp < time.monotonic():
                del self._store[key]
                return None
            return val

    def set(self, key: str, value: object, ttl_s: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl_s, value)

    def invalidate(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                del self._store[k]


def _watchlist_path() -> Path:
    from app.config import settings

    p = Path(settings.data_dir) / "user_data" / "fund_watchlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_watchlist() -> list[dict]:
    p = _watchlist_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("fund_watchlist.json 读取失败: %s", e)
        return []


def save_watchlist(items: list[dict]) -> None:
    p = _watchlist_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
