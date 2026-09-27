"""市场数据 API 路由 (prefix: /api/custom/market-flow)。

鉴权由全局 auth_middleware 统一处理 (/api/* 需登录)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.custom.market_flow import client

logger = logging.getLogger(__name__)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/market-flow", tags=["custom-market-flow"])

    @router.get("/popularity")
    def popularity(limit: int = Query(100, ge=1, le=500)) -> dict:
        """人气排行。"""
        return {"items": client.get_popularity(limit=limit)}

    @router.get("/money-flow")
    def money_flow(limit: int = Query(100, ge=1, le=500)) -> dict:
        """资金流向 (全市场净流入排行)。"""
        return {"items": client.get_money_flow(limit=limit)}

    @router.get("/money-flow-main")
    def money_flow_main(limit: int = Query(100, ge=1, le=500)) -> dict:
        """主力资金净流入排行。"""
        return {"items": client.get_money_flow_main(limit=limit)}

    @router.get("/concepts/{symbol}")
    def concepts(symbol: str) -> dict:
        """个股同花顺概念。symbol 如 600900.SH。"""
        return {"symbol": symbol.upper(), "concepts": client.get_concepts(symbol.upper())}

    @router.get("/industries/{symbol}")
    def industries(symbol: str) -> dict:
        """个股同花顺行业。"""
        return {"symbol": symbol.upper(), "industries": client.get_industries(symbol.upper())}

    return router
