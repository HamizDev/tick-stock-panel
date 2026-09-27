"""AI 基金分析服务 — 净值走势 / 基金资料 / 风险收益分析。

职责:
    组合一只基金的净值序列(单位净值走势)+ 基金基本资料 →
    拼装客观基金分析系统提示词 → 流式调用 LLM → 逐 chunk 吐给前端。

与 stock_analyzer.py 的区别:
    - 数据源: 基金净值 (无 OHLC, 只有单位净值/日增长率)
    - 无 K 线技术指标 (MACD/KDJ/布林等不适用)、无关键价位
    - 核心维度: 净值走势、回撤与波动、基金经理与公司、规模与费用

不知道: HTTP、前端、配置持久化。
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位拥有 15 年公募基金研究经验的基金分析师,擅长从净值走势、回撤特征、基金经理与基金公司维度客观解读一只基金的状态。你的任务是:基于提供的基金净值数据与基本资料,产出一份客观、中立、不包含任何买卖或操作建议的基金分析报告。

## 核心红线(务必遵守)

- 绝对不输出"买入/卖出/加仓/减仓/观望/定投/赎回/建议持有"等任何交易指令或倾向性措辞
- 你的角色是客观陈述该基金当前的净值状态、风险收益特征与潜在风险,让读者自行判断
- 绝对不要编造基金经理变更、规模、分红等未提供的数据

## 输出规范

用 Markdown 格式输出,严格遵循以下结构。不要输出任何 JSON 或代码块,直接输出 Markdown 正文。

### 1. 一句话定调(1-2 句)

用一句话概括该基金当前的净值状态(如"近一年净值震荡上行,最大回撤控制较好"/"近期净值持续回调,波动有所加大")。不评价好坏、不下操作结论。

### 2. 净值走势分析(核心维度)

基于提供的净值序列,客观陈述:

- 收益表现:近 1 月/3 月/6 月/1 年的区间涨跌幅 (用数据说话)
- 波动与回撤:最大回撤幅度、净值波动率的客观水平
- 阶段特征:近期是单边趋势还是震荡,是否有加速/放缓迹象

每条结论必须引用具体数值,客观陈述,不下好坏定性。

### 3. 基金经理与公司(资料维度)

基于提供的基本资料,客观列出:

- 基金经理姓名、任职时间 (如有)
- 基金公司 (如有)
- 基金类型、成立日期、业绩基准 (如有)

只陈述资料中的客观事实,不评价经理能力强弱。

### 4. 风险提示

2-3 条,只做客观描述:

- 该基金历史最大回撤水平与当前回撤位置
- 波动率特征
- 费率 (管理费/托管费/申购赎回费) 的客观水平 (如有数据)

## 分析准则(务必遵守)

- 所有数字必须来自提供的数据,不得编造
- 不预测未来走势,不下"值得长期持有"等结论
- 保持客观中立,像一份基金研究简报
"""


def _build_user_prompt(nav_tail, profile, thscode, name, focus=""):
    """构建用户消息: 基金代码 + 名称 + 净值 JSON + 资料 + 关注点。"""
    parts = [
        "基金代码: " + thscode,
        "基金名称: " + name,
        "",
        "以下是该基金近期单位净值数据(JSON,含净值日期、单位净值、日增长率,升序):",
        "```json",
        json.dumps(nav_tail, ensure_ascii=False),
        "```",
    ]
    if profile:
        parts.extend([
            "",
            "以下是该基金基本资料(JSON):",
            "```json",
            json.dumps(profile, ensure_ascii=False),
            "```",
        ])
    else:
        parts.extend([
            "",
            "(该基金暂无基本资料,请仅基于净值数据做分析,不要编造基金经理/规模等信息。)",
        ])
    if focus:
        parts.extend(["", "用户特别关注: " + focus])
    return "\n".join(parts)


def _calc_stats(nav):
    """计算净值序列的简单统计: 区间涨跌幅、最大回撤。"""
    vals = [float(r.get("unit_nav") or 0) for r in nav if r.get("unit_nav")]
    if len(vals) < 2:
        return {}
    stats = {}
    for label, n in [("近1月", 22), ("近3月", 66), ("近6月", 132), ("近1年", 250)]:
        if len(vals) >= n:
            base = vals[-n]
            if base > 0:
                stats[label] = round((vals[-1] - base) / base * 100, 2)
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    stats["最大回撤%"] = round(max_dd * 100, 2)
    stats["最新净值"] = vals[-1]
    stats["样本天数"] = len(vals)
    return stats


async def analyze_fund_stream(nav_rows, profile, thscode, name, focus=""):
    """流式基金分析: yield 出每个 NDJSON 事件。

    协议(与 stock_analyzer 一致):
        {"type":"meta","symbol","summary","stats"}
        {"type":"delta","content":"..."}
        {"type":"error","message":"..."}
        {"type":"done"}
    """
    nav_tail = nav_rows[-250:] if len(nav_rows) > 250 else nav_rows
    if not nav_tail:
        yield json.dumps({
            "type": "error",
            "message": "基金 " + thscode + " 暂无净值数据",
        }, ensure_ascii=False)
        return

    stats = _calc_stats(nav_tail)
    summary = (
        "最新净值 " + str(stats.get("最新净值"))
        + ", 近1年 " + str(stats.get("近1年", "N/A")) + "%"
        + ", 最大回撤 " + str(stats.get("最大回撤%", "N/A")) + "%"
    )

    yield json.dumps({
        "type": "meta",
        "symbol": thscode,
        "summary": summary,
        "stats": stats,
    }, ensure_ascii=False)

    try:
        from app.services.ai_provider import stream_ai_text

        clean_nav = [
            {
                "date": r.get("date") or r.get("nav_date"),
                "unit_nav": r.get("unit_nav"),
                "growth": r.get("growth"),
            }
            for r in nav_tail
        ]
        user_prompt = _build_user_prompt(clean_nav, profile, thscode, name, focus)
        async for delta in stream_ai_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=None,
            prefer_final_answer=True,
        ):
            yield json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)

    except Exception as e:
        logger.exception("AI fund analysis failed for %s: %s", thscode, e)
        yield json.dumps({"type": "error", "message": "AI 分析失败: " + str(e)}, ensure_ascii=False)
        return

    yield json.dumps({"type": "done"}, ensure_ascii=False)
