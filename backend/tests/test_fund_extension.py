"""基金中心扩展契约测试 (不依赖真实网络与 API Key)。

覆盖:
1. 字段映射与单位转换: 百分数→小数制、volume 股/份→手、缺失字段置 None 不伪造
2. ETF 日K前复权如实标注 (adjusted=forward), 不冒充原始价
3. 空数据 / 缺字段 / 接口业务错误 (3004) 的软失败语义
4. get_realtime_etfs: 映射、上限 60、异常 → None (与空列表区分)
5. 自选持久化 roundtrip
"""

from __future__ import annotations

import pytest

from app.custom.fund import service as svc
from app.custom.fund.client import FundError, FuyaoFundClient
from app.plugins.fuyao import client as fc
from app.plugins.fuyao.provider import FuyaoProvider

# ---------- map_quote ----------

_FUND_ROW = {
    "thscode": "510300.SH",
    "ticker": "510300",
    "last_price": 4.753,
    "open_price": 4.775,
    "high_price": 4.825,
    "low_price": 4.724,
    "prev_price": 4.838,
    "price_change_ratio_pct": -1.756924,  # 百分数原值
    "price_change": -0.085,
    "price_amplitude_ratio_pct": 2.08764,
    "volume": 1657822800,  # 股
    "turnover": 7909234100,
    "turnover_ratio_pct": 9.012068,
    "timestamp": 1784210584000,
}


def test_map_quote_units():
    q = svc.map_quote(_FUND_ROW, name="沪深300ETF")
    assert q["symbol"] == "510300.SH"
    assert q["name"] == "沪深300ETF"
    # 百分数 → 小数制
    assert q["change_pct"] == pytest.approx(-1.756924 / 100)
    assert q["amplitude"] == pytest.approx(2.08764 / 100)
    assert q["turnover_rate"] == pytest.approx(9.012068 / 100)
    # 股 → 手
    assert q["volume"] == 1657822800 // 100
    assert q["amount"] == 7909234100
    assert q["prev_close"] == 4.838


def test_map_quote_derives_change_pct():
    row = dict(_FUND_ROW)
    row.pop("price_change_ratio_pct")
    q = svc.map_quote(row)
    # change_pct = change_amount / prev_close (小数制, 不乘 100)
    assert q["change_pct"] == pytest.approx(-0.085 / 4.838)


def test_map_quote_missing_fields_are_none_not_fabricated():
    q = svc.map_quote({"thscode": "510300.SH", "last_price": 4.75})
    assert q["prev_close"] is None
    assert q["change_pct"] is None  # 无法推导时置 None, 不启发式补全
    assert q["volume"] is None
    assert q["amplitude"] is None


def test_map_quote_no_thscode_returns_none():
    assert svc.map_quote({"last_price": 1.0}) is None


# ---------- map_kline ----------

def test_map_kline_marks_forward_adjusted():
    rows = [
        {"date_ms": 1784150400000, "open_price": 4.78, "high_price": 4.83,
         "low_price": 4.72, "close_price": 4.75, "volume": 1000000, "turnover": 4750000},
    ]
    out = svc.map_kline(rows)
    # 关键契约: 上游前复权, 必须如实标注, 不得冒充不复权原始价
    assert out["adjusted"] == "forward"
    bar = out["bars"][0]
    assert bar["close"] == 4.75
    assert bar["volume"] == 10000  # 股 → 手
    assert len(bar["date"]) == 10  # YYYY-MM-DD


def test_map_kline_empty_and_bad_dates():
    assert svc.map_kline([]) == {"adjusted": "forward", "bars": []}
    out = svc.map_kline([{"date_ms": "bad", "close_price": 1.0}])
    assert out["bars"] == []  # 无效日期整行跳过, 不抛异常


# ---------- map_nav ----------

def test_map_nav_sorted_and_empty():
    rows = [
        {"nav_date": 1784150400000, "unit_nav": 1.4753, "adj_nav": 1.4801},
        {"nav_date": 1784064000000, "unit_nav": 1.4700, "adj_nav": 1.4748},
    ]
    nav = svc.map_nav(rows)
    assert [n["nav_date"] for n in nav] == sorted(n["nav_date"] for n in nav)
    assert nav[0]["unit_nav"] == 1.47
    assert svc.map_nav([]) == []
    # 缺 nav_date 的行跳过
    assert svc.map_nav([{"unit_nav": 1.0}]) == []


def test_map_search_kind_labels():
    rows = [
        {"thscode": "005827.OF", "ticker": "005827", "name": "易方达蓝筹精选",
         "asset_type": "fund-otc"},
        {"thscode": "510300.SH", "ticker": "510300", "name": "沪深300ETF",
         "asset_type": "fund-etf"},
    ]
    items = svc.map_search(rows)
    assert items[0]["kind_label"] == "场外基金"
    assert items[1]["kind_label"] == "ETF"
    assert svc.map_search([{"ticker": "x"}]) == []  # 无 thscode 跳过


# ---------- TTLCache ----------

def test_ttl_cache_hit_expire_invalidate():
    c = svc.TTLCache()
    c.set("a", 1, ttl_s=60)
    assert c.get("a") == 1
    c.set("b", 2, ttl_s=-1)  # 已过期
    assert c.get("b") is None
    c.set("quote:X", 1, ttl_s=60)
    c.set("nav:X", 2, ttl_s=60)
    c.invalidate("quote:")
    assert c.get("quote:X") is None
    assert c.get("nav:X") == 2


# ---------- FuyaoFundClient: 3004 软失败 ----------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, payload):
        self._payload = payload

    def get(self, url, params=None, headers=None):
        return _FakeResp(self._payload)


def _fund_client(payload: dict) -> FuyaoFundClient:
    c = FuyaoFundClient(api_key="test-key")
    c._client = _FakeHTTP(payload)
    return c


def test_snapshot_3004_returns_none_not_error():
    """场外基金调快照端点: 上游 code=3004 → None, 不是异常。"""
    c = _fund_client({"code": 3004, "message": "This fund does not support market data"})
    assert c.snapshot("005827.OF") is None


def test_snapshot_3001_stock_returns_none():
    """股票代码调快照端点: 上游 code=3001 → None (单只跳过), 不是异常。"""
    c = _fund_client({"code": 3001, "message": "Fund not found: 600519.SH"})
    assert c.snapshot("600519.SH") is None


def test_snapshot_other_error_raises():
    c = _fund_client({"code": 1002, "message": "Unknown thscode"})
    with pytest.raises(FundError, match="code=1002"):
        c.snapshot("XXXX.XX")


def test_snapshot_ok_returns_first_item():
    c = _fund_client({"code": 0, "data": {"timestamp": 1, "item": [_FUND_ROW]}})
    assert c.snapshot("510300.SH")["thscode"] == "510300.SH"


# ---------- FuyaoProvider.get_realtime_etfs ----------

class _FakeFundClient:
    def __init__(self, rows: dict[str, dict | None], error: Exception | None = None):
        self.rows = rows
        self.error = error
        self.calls: list[str] = []

    def fund_snapshot(self, thscode: str):
        self.calls.append(thscode)
        if self.error:
            raise self.error
        # 真实 client 的 3004 语义: 内部消化返回 None (单只跳过), 不抛异常
        return self.rows.get(thscode)


def _etf_provider(monkeypatch, fake: _FakeFundClient) -> FuyaoProvider:
    p = FuyaoProvider()
    monkeypatch.setattr(p, "_get_client", lambda: fake)
    return p


def test_get_realtime_etfs_mapping(monkeypatch):
    fake = _FakeFundClient({"510300.SH": dict(_FUND_ROW)})
    p = _etf_provider(monkeypatch, fake)
    recs = p.get_realtime_etfs(["510300.SH"])
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "510300.SH"
    assert r["change_pct"] == pytest.approx(-1.756924 / 100)
    assert r["volume"] == 1657822800 // 100  # 股 → 手
    assert r["amplitude"] == pytest.approx(2.08764 / 100)
    assert r["turnover_rate"] == pytest.approx(9.012068 / 100)


def test_get_realtime_etfs_skips_non_etf(monkeypatch):
    """股票/场外基金 (3004) 单只跳过, 不影响其他。"""
    fake = _FakeFundClient({"510300.SH": dict(_FUND_ROW)})
    p = _etf_provider(monkeypatch, fake)
    recs = p.get_realtime_etfs(["600519.SH", "510300.SH", "005827.OF"])
    assert [r["symbol"] for r in recs] == ["510300.SH"]
    assert fake.calls == ["600519.SH", "510300.SH", "005827.OF"]


def test_get_realtime_etfs_empty_and_error(monkeypatch):
    p = _etf_provider(monkeypatch, _FakeFundClient({}))
    assert p.get_realtime_etfs([]) == []  # 空输入 → 空列表
    p2 = _etf_provider(monkeypatch, _FakeFundClient({}, error=fc.FuyaoError("限流")))
    assert p2.get_realtime_etfs(["510300.SH"]) is None  # 异常 → None (保上轮缓存)


def test_get_realtime_etfs_caps_at_60(monkeypatch):
    fake = _FakeFundClient({})
    p = _etf_provider(monkeypatch, fake)
    p.get_realtime_etfs([f"{i:06d}.SH" for i in range(100)])
    assert len(fake.calls) == 60


# ---------- watchlist 持久化 ----------

def test_watchlist_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "fund_watchlist.json"
    monkeypatch.setattr(svc, "_watchlist_path", lambda: p)
    assert svc.load_watchlist() == []
    items = [{"thscode": "005827.OF", "name": "易方达蓝筹精选", "added_at": 1}]
    svc.save_watchlist(items)
    assert svc.load_watchlist() == items
    # 坏文件不抛异常, 返回 []
    p.write_text("not json", encoding="utf-8")
    assert svc.load_watchlist() == []
