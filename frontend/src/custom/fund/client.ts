/**
 * 基金中心自包含 HTTP 客户端。
 *
 * 取舍同 assistant 扩展: 不修改核心 lib/api.ts, 在此内建同款约定的最小
 * 客户端 (同源 fetch + credentials + 统一错误文案)。后端见
 * backend/app/custom/fund/routes.py。
 */

export interface FundSearchItem {
  thscode: string
  ticker: string | null
  name: string | null
  asset_type: string
  kind_label: string
}

export interface FundWatchItem {
  thscode: string
  name: string | null
  asset_type: string | null
  kind_label: string
  added_at: number
}

export interface FundQuote {
  thscode: string
  symbol: string
  name: string | null
  last_price: number | null
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  /** 小数制, 0.0174 = +1.74% */
  change_pct: number | null
  change_amount: number | null
  /** 手 */
  volume: number | null
  amount: number | null
  amplitude: number | null
  turnover_rate: number | null
  timestamp: number | null
}

export interface FundKBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  amount: number | null
}

export interface FundNavPoint {
  nav_date: string
  unit_nav: number | null
  adj_nav: number | null
}

export interface FundProfile {
  thscode: string | null
  ticker: string | null
  fund_name: string | null
  estab_date: string | null
  mgmt_name: string | null
  manager_name: string | null
  fund_type: string | null
  benchmark: string | null
}

const BASE = '/api/custom/fund'

/** 网关层瞬断 (免费隧道闪断) 值得重试的状态码。 */
const RETRYABLE_STATUS = new Set([502, 503, 504])
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** 隧道闪断时服务端直接掐连接, 先抛出来由外层判断是否重试。 */
class TransientGatewayError extends Error {
  constructor(readonly status: number) {
    super(`gateway ${status}`)
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  // 只对幂等 GET/HEAD 自动重试, 避免 POST/DELETE 重复提交
  const idempotent = method === 'GET' || method === 'HEAD'
  for (let attempt = 1; ; attempt++) {
    try {
      return await doReq<T>(path, init)
    } catch (err) {
      const transient =
        err instanceof TransientGatewayError ||
        err instanceof TypeError // fetch 裸网络错误: Failed to fetch 等
      if (!idempotent || !transient || attempt >= 3) throw err
      await sleep(700 * attempt)
    }
  }
}

async function doReq<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    if (RETRYABLE_STATUS.has(res.status)) throw new TransientGatewayError(res.status)
    let detail = ''
    try {
      const parsed = (await res.json()) as { detail?: string }
      detail = parsed.detail ?? ''
    } catch {
      /* 忽略解析错误 */
    }
    throw new Error(detail || `基金接口请求失败: ${res.status}`)
  }
  return (await res.json()) as T
}

export const fundApi = {
  search(q: string, limit = 20): Promise<{ items: FundSearchItem[] }> {
    return req(`/search?q=${encodeURIComponent(q)}&limit=${limit}`)
  },
  watchlist(): Promise<{ items: FundWatchItem[] }> {
    return req('/watchlist')
  },
  addWatch(item: { thscode: string; name?: string | null; asset_type?: string | null }) {
    return req<{ items: FundWatchItem[] }>('/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(item),
    })
  },
  removeWatch(thscode: string) {
    return req<{ items: FundWatchItem[] }>(`/watchlist/${encodeURIComponent(thscode)}`, {
      method: 'DELETE',
    })
  },
  quote(thscode: string): Promise<{ quote: FundQuote | null }> {
    return req(`/quote/${encodeURIComponent(thscode)}`)
  },
  kline(thscode: string, days = 250): Promise<{ thscode: string; adjusted: string; bars: FundKBar[] }> {
    return req(`/kline/${encodeURIComponent(thscode)}?days=${days}`)
  },
  nav(thscode: string, range = 'year'): Promise<{ thscode: string; nav: FundNavPoint[] }> {
    return req(`/nav/${encodeURIComponent(thscode)}?range=${range}`)
  },
  profile(thscode: string): Promise<{ profile: FundProfile | null }> {
    return req(`/profile/${encodeURIComponent(thscode)}`)
  },
}

/** 小数制 → 百分比文案, null 显示 — */
export function pctText(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}
