"""基金中心 API 路由 (prefix: /api/custom/fund)。

鉴权由全局 auth_middleware 统一处理 (/api/* 需登录)。
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.custom.fund import service as svc
from app.custom.fund.client import FundError, FundNotConfiguredError, FuyaoFundClient

logger = logging.getLogger(__name__)

_cache = svc.TTLCache()
_client_lock = threading.Lock()
_client: FuyaoFundClient | None = None

_NAV_RANGES = {"week", "month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"}
_MAX_KLINE_DAYS = 5 * 365


def _client_or_503() -> FuyaoFundClient:
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = FuyaoFundClient()
            except FundNotConfiguredError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
        return _client


def _watchlist_names() -> dict[str, str]:
    try:
        return {w["thscode"]: w.get("name", "") for w in svc.load_watchlist()}
    except Exception:
        return {}


class WatchAddIn(BaseModel):
    thscode: str
    name: str | None = None
    asset_type: str | None = None


class AnalyzeIn(BaseModel):
    thscode: str
    focus: str = ""


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/fund", tags=["custom-fund"])

    @router.get("/search")
    def search(
        q: str = Query(..., min_length=1, max_length=40),
        types: str = Query("fund-otc,fund-etf,fund-lof"),
        limit: int = Query(20, ge=1, le=50),
    ) -> dict:
        """基金/ETF 检索: q=代码或名称子串。fuyao 搜不到时用 AkShare 本地补充。"""
        client = _client_or_503()
        try:
            rows = client.search(q.strip(), asset_types=types, limit=limit)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        # fuyao 中文模糊搜索弱 (如"南方信息"返回 0 条)，用 AkShare 本地补充
        # (AkShare 返回的名称已是完整简称，跳过逐条修正，省 30+s)
        if not rows:
            ak_rows = svc.akshare_search(q.strip(), limit=limit)
            if ak_rows:
                return {"items": svc.map_search(ak_rows, skip_akshare=True)}
        return {"items": svc.map_search(rows)}

    @router.get("/watchlist")
    def watchlist() -> dict:
        items = svc.load_watchlist()
        # 读取时动态修正名称 (自选存的是添加时的旧名, 可能是 fuyao 截断名)
        for it in items:
            thscode = it.get("thscode") or ""
            if thscode:
                ak_name = svc._akshare_name(thscode)
                if ak_name:
                    it["name"] = ak_name
        return {"items": items}

    @router.post("/watchlist")
    def watchlist_add(req: WatchAddIn) -> dict:
        thscode = req.thscode.strip().upper()
        if not thscode:
            raise HTTPException(status_code=400, detail="thscode 不能为空")
        items = svc.load_watchlist()
        if not any(w.get("thscode") == thscode for w in items):
            items.append(
                {
                    "thscode": thscode,
                    "name": req.name,
                    "asset_type": req.asset_type,
                    "kind_label": svc.ASSET_KIND_LABEL.get(req.asset_type or "", ""),
                    "added_at": int(time.time()),
                }
            )
            svc.save_watchlist(items)
        return {"items": items}

    @router.delete("/watchlist/{thscode}")
    def watchlist_del(thscode: str) -> dict:
        items = [w for w in svc.load_watchlist() if w.get("thscode") != thscode.upper()]
        svc.save_watchlist(items)
        return {"items": items}

    @router.get("/quote/{thscode}")
    def quote(thscode: str) -> dict:
        """场内基金(ETF/LOF)实时快照。场外基金返回 null(无场内行情)。"""
        thscode = thscode.strip().upper()
        key = f"quote:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return {"quote": hit}
        client = _client_or_503()
        try:
            row = client.snapshot(thscode)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        q = svc.map_quote(row, name=_watchlist_names().get(thscode)) if row else None
        _cache.set(key, q, ttl_s=15)
        return {"quote": q}

    @router.get("/kline/{thscode}")
    def kline(
        thscode: str, days: int = Query(250, ge=5, le=_MAX_KLINE_DAYS)
    ) -> dict:
        """场内基金历史日线。上游为前复权口径, 如实标注 adjusted=forward。"""
        thscode = thscode.strip().upper()
        key = f"kline:{thscode}:{days}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _client_or_503()
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86400 * 1000
        try:
            rows = client.kline(thscode, start_ms, end_ms)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        out = {"thscode": thscode, **svc.map_kline(rows)}
        _cache.set(key, out, ttl_s=3600)
        return out

    @router.get("/nav/{thscode}")
    def nav(
        thscode: str, range: str = Query("year"), nav_type: str = Query("unit,adj")
    ) -> dict:
        """基金净值序列 (场外基金 T-1 净值; ETF 也可用)。"""
        thscode = thscode.strip().upper()
        if range not in _NAV_RANGES:
            raise HTTPException(status_code=400, detail=f"range 非法: {range}")
        key = f"nav:{thscode}:{range}:{nav_type}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _client_or_503()
        try:
            rows = client.nav(thscode, range_=range, nav_type=nav_type)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        out = {"thscode": thscode, "nav": svc.map_nav(rows)}
        _cache.set(key, out, ttl_s=3600)
        return out

    @router.get("/profile/{thscode}")
    def profile(thscode: str) -> dict:
        """基金基本资料。"""
        thscode = thscode.strip().upper()
        key = f"profile:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return {"profile": hit}
        client = _client_or_503()
        try:
            row = client.profile(thscode)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        p = svc.map_profile(row) if row else None
        _cache.set(key, p, ttl_s=86400)
        return {"profile": p}

    @router.post("/analyze")
    async def analyze(req: AnalyzeIn):
        """AI 基金分析 (流式 NDJSON，与 /api/stock-analysis/analyze 协议一致)。"""
        from fastapi.responses import StreamingResponse

        from app.custom.fund import analyzer as fund_analyzer
        from app.services.ndjson_heartbeat import with_heartbeat

        thscode = req.thscode.strip().upper()
        client = _client_or_503()

        # 净值 (优先用缓存的 nav 接口)
        try:
            nav_rows = client.nav(thscode, range_="year", nav_type="unit,adj")
        except FundError:
            nav_rows = []
        nav = svc.map_nav(nav_rows)

        # 资料
        try:
            row = client.profile(thscode)
            profile = svc.map_profile(row) if row else None
        except FundError:
            profile = None

        # 名称 (自选表 > AkShare > thscode)
        name = _watchlist_names().get(thscode) or svc._akshare_name(thscode) or thscode

        async def _gen():
            async for chunk in fund_analyzer.analyze_fund_stream(
                nav, profile, thscode, name, req.focus or ""
            ):
                yield chunk + "\n"

        return StreamingResponse(with_heartbeat(_gen()), media_type="application/x-ndjson")

    return router
