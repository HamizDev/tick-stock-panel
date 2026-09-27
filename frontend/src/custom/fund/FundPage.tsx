/**
 * 基金中心页面 — 场外基金净值 + ETF/LOF 行情。
 *
 * 自包含扩展页面: 数据全部走 /api/custom/fund, 状态用组件内 state +
 * 手动轮询, 不碰核心 queryKeys / api.ts / 全局 store。删除本目录即卸载。
 *
 * 样式: 与主面板 Dashboard 一致 (accent 色条标题 / surface 卡片 /
 * 紧凑字号 / 数字 font-mono)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'
import { ArrowLeft, BarChart3, Info, LineChart, Plus, Search, Star, Activity } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { cn } from '@/lib/cn'
import {
  fundApi,
  pctText,
  type FundNavPoint,
  type FundProfile,
  type FundQuote,
  type FundSearchItem,
  type FundWatchItem,
} from './client'

const QUOTE_POLL_MS = 15000

function isTradable(kind: string): boolean {
  return kind === 'ETF' || kind === 'LOF'
}

/** 主面板风格区块标题: accent 色条 + Icon + text-xs 标题 */
function SectionTitle({ icon: Icon, title }: { icon: typeof Activity; title: string }) {
  return (
    <div className="mb-1.5 flex items-center gap-1.5">
      <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
      <Icon className="h-3.5 w-3.5 text-accent" />
      <h2 className="text-xs font-semibold text-foreground">{title}</h2>
    </div>
  )
}

/** 主面板风格卡片 */
function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('rounded-md border border-border/40 bg-surface/60 p-2.5', className)}>
      {children}
    </div>
  )
}

function useWatchlist() {
  const [items, setItems] = useState<FundWatchItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try {
      const r = await fundApi.watchlist()
      setItems(r.items)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    refresh()
  }, [refresh])
  return { items, loading, error, refresh }
}

function NavChart({ nav }: { nav: FundNavPoint[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    if (!chartRef.current) chartRef.current = echarts.init(ref.current)
    const chart = chartRef.current
    const dates = nav.map((p) => p.nav_date)
    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis', textStyle: { fontSize: 11 } },
      legend: { data: ['单位净值', '复权净值'], textStyle: { color: '#9aa4b2', fontSize: 10 } },
      grid: { left: 40, right: 12, top: 26, bottom: 22 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
      series: [
        {
          name: '单位净值',
          type: 'line',
          showSymbol: false,
          data: nav.map((p) => p.unit_nav),
          lineStyle: { color: '#4f8cff', width: 1.5 },
        },
        {
          name: '复权净值',
          type: 'line',
          showSymbol: false,
          data: nav.map((p) => p.adj_nav),
          lineStyle: { color: '#f0a35e', width: 1.5 },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [nav])

  useEffect(() => () => chartRef.current?.dispose(), [])

  return <div ref={ref} className="h-52 w-full" />
}

function QuoteCard({ quote }: { quote: FundQuote }) {
  const up = (quote.change_pct ?? 0) >= 0
  return (
    <Card>
      <SectionTitle icon={Activity} title="实时行情" />
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-lg font-bold tabular-nums">
          {quote.last_price?.toFixed(3) ?? '—'}
        </span>
        <span className={cn('font-mono text-[11px] tabular-nums', up ? 'text-bull' : 'text-bear')}>
          {quote.change_amount !== null && quote.change_amount !== undefined
            ? `${up ? '+' : ''}${quote.change_amount.toFixed(3)}`
            : '—'}{' '}
          {pctText(quote.change_pct)}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-4 gap-1.5 text-[11px]">
        {[
          ['开盘', quote.open?.toFixed(3)],
          ['最高', quote.high?.toFixed(3)],
          ['最低', quote.low?.toFixed(3)],
          ['昨收', quote.prev_close?.toFixed(3)],
          ['振幅', pctText(quote.amplitude)],
          ['换手', pctText(quote.turnover_rate)],
          ['成交量(手)', quote.volume?.toLocaleString()],
          ['成交额', quote.amount ? `${(quote.amount / 1e8).toFixed(2)}亿` : undefined],
        ].map(([k, v]) => (
          <div key={k}>
            <div className="text-[10px] text-muted-foreground">{k}</div>
            <div className="font-mono tabular-nums">{v ?? '—'}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function ProfileBlock({ profile }: { profile: FundProfile }) {
  const rows: [string, string | null][] = [
    ['基金全称', profile.fund_name],
    ['基金公司', profile.mgmt_name],
    ['基金经理', profile.manager_name],
    ['成立日期', profile.estab_date],
    ['基金类型', profile.fund_type],
  ]
  return (
    <Card>
      <SectionTitle icon={Info} title="基本资料" />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-1.5">
            <dt className="shrink-0 text-muted-foreground">{k}</dt>
            <dd className="truncate">{v ?? '—'}</dd>
          </div>
        ))}
      </dl>
      {profile.benchmark && (
        <div className="mt-1 text-[10px] text-muted-foreground">业绩基准: {profile.benchmark}</div>
      )}
    </Card>
  )
}

function FundDetail({ item, onBack }: { item: FundWatchItem | FundSearchItem; onBack: () => void }) {
  const thscode = item.thscode
  const tradable = isTradable('kind_label' in item ? item.kind_label : '')
  const [quote, setQuote] = useState<FundQuote | null>(null)
  const [kline, setKline] = useState<{ adjusted: string; bars: { date: string; open: number | null; high: number | null; low: number | null; close: number | null; volume: number | null }[] } | null>(null)
  const [nav, setNav] = useState<FundNavPoint[] | null>(null)
  const [profile, setProfile] = useState<FundProfile | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    setErr('')
    setQuote(null)
    setKline(null)
    setNav(null)
    setProfile(null)
    fundApi
      .profile(thscode)
      .then((r) => alive && setProfile(r.profile))
      .catch(() => {})
    if (tradable) {
      fundApi
        .quote(thscode)
        .then((r) => alive && setQuote(r.quote))
        .catch((e) => alive && setErr(e instanceof Error ? e.message : '行情加载失败'))
      fundApi
        .kline(thscode, 250)
        .then((r) => alive && setKline(r))
        .catch(() => {})
    } else {
      fundApi
        .nav(thscode, 'year')
        .then((r) => alive && setNav(r.nav))
        .catch((e) => alive && setErr(e instanceof Error ? e.message : '净值加载失败'))
    }
    return () => {
      alive = false
    }
  }, [thscode, tradable])

  // ETF/LOF 轮询实时快照
  useEffect(() => {
    if (!tradable) return
    const t = setInterval(async () => {
      try {
        const r = await fundApi.quote(thscode)
        setQuote(r.quote)
      } catch {
        /* 轮询失败静默, 保留上次数据 */
      }
    }, QUOTE_POLL_MS)
    return () => clearInterval(t)
  }, [thscode, tradable])

  const ohlc = useMemo(
    () =>
      (kline?.bars ?? [])
        .filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null)
        .map((b) => ({
          date: b.date,
          open: b.open as number,
          high: b.high as number,
          low: b.low as number,
          close: b.close as number,
          volume: b.volume ?? undefined,
        })),
    [kline],
  )

  return (
    <div className="space-y-2.5">
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" />
        返回基金中心
      </button>
      <div className="flex items-center gap-1.5">
        <h2 className="text-xs font-semibold text-foreground">{item.name ?? thscode}</h2>
        <span className="rounded bg-muted px-1.5 py-px font-mono text-[10px] text-muted-foreground">
          {thscode}
        </span>
      </div>
      {err && <div className="text-[11px] text-bear">{err}</div>}
      {profile && <ProfileBlock profile={profile} />}
      {tradable && quote && <QuoteCard quote={quote} />}
      {tradable && kline && (
        <Card>
          <div className="mb-1.5 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded-full bg-gradient-to-b from-accent to-accent/30" />
              <BarChart3 className="h-3.5 w-3.5 text-accent" />
              <h2 className="text-xs font-semibold text-foreground">日K</h2>
            </div>
            <span className="text-[10px] text-muted-foreground">前复权口径 (数据源仅提供前复权)</span>
          </div>
          {ohlc.length > 0 ? (
            <EChartsCandlestick data={ohlc} height={300} showMA showInfoBar={false} showMarkers={false} />
          ) : (
            <div className="py-6 text-center text-[11px] text-muted-foreground">暂无K线数据</div>
          )}
        </Card>
      )}
      {!tradable && (
        <Card>
          <SectionTitle icon={LineChart} title="净值走势 (近一年)" />
          {nav && nav.length > 0 ? (
            <NavChart nav={nav} />
          ) : (
            <div className="py-6 text-center text-[11px] text-muted-foreground">
              {nav ? '暂无净值数据' : '加载中…'}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

export function FundPage() {
  const { items, loading, error, refresh } = useWatchlist()
  const [q, setQ] = useState('')
  const [results, setResults] = useState<FundSearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [selected, setSelected] = useState<FundWatchItem | FundSearchItem | null>(null)

  const doSearch = useCallback(async () => {
    const kw = q.trim()
    if (!kw) {
      setResults([])
      setSearched(false)
      return
    }
    setSearching(true)
    setSearched(true)
    try {
      const r = await fundApi.search(kw)
      setResults(r.items)
    } catch {
      setResults([])
    } finally {
      setSearching(false)
    }
  }, [q])

  const addWatch = useCallback(
    async (it: FundSearchItem) => {
      await fundApi.addWatch({ thscode: it.thscode, name: it.name, asset_type: it.asset_type })
      refresh()
    },
    [refresh],
  )

  const inWatch = useMemo(() => new Set(items.map((w) => w.thscode)), [items])
  const etfItems = items.filter((w) => isTradable(w.kind_label))
  const otcItems = items.filter((w) => !isTradable(w.kind_label))

  if (selected) {
    return (
      <div className="mx-auto max-w-5xl space-y-2.5 p-2.5">
        <FundDetail item={selected} onBack={() => setSelected(null)} />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl space-y-2.5 p-2.5">
      <SectionTitle icon={Star} title="基金中心" />

      {/* 搜索 */}
      <Card>
        <div className="flex gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
              placeholder="搜索基金/ETF: 代码或名称, 如 005827 / 沪深300"
              className="h-7 w-full rounded-md border border-border/40 bg-background pl-7 pr-2 text-[11px] outline-none focus:border-accent"
            />
          </div>
          <button
            onClick={doSearch}
            disabled={searching}
            className="h-7 shrink-0 rounded-md bg-accent px-3 text-[11px] font-medium text-white disabled:opacity-50"
          >
            {searching ? '搜索中…' : '搜索'}
          </button>
        </div>
        {searched && !searching && results.length === 0 && (
          <div className="mt-1.5 text-[11px] text-muted-foreground">
            未找到相关基金，换个关键词或直接搜代码试试（如 510300）。
          </div>
        )}
        {results.length > 0 && (
          <ul className="mt-1.5 divide-y divide-border/40">
            {results.map((r) => (
              <li key={r.thscode} className="flex items-center gap-2 py-1.5">
                <button
                  onClick={() => setSelected(r)}
                  className="flex flex-1 items-center gap-1.5 text-left min-w-0"
                >
                  <span className="text-[11px] font-medium hover:text-accent break-words">{r.name ?? r.thscode}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{r.thscode}</span>
                  <span className="rounded bg-muted px-1 py-px text-[10px] text-muted-foreground">
                    {r.kind_label}
                  </span>
                </button>
                {inWatch.has(r.thscode) ? (
                  <span className="text-[10px] text-muted-foreground">已在自选</span>
                ) : (
                  <button
                    onClick={() => addWatch(r)}
                    className="flex items-center gap-0.5 rounded-md border border-border/40 px-1.5 py-0.5 text-[10px] hover:border-accent hover:text-accent"
                  >
                    <Plus className="h-3 w-3" />
                    自选
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 自选 */}
      <Card>
        <SectionTitle icon={Star} title="我的自选" />
        {loading && <div className="text-[11px] text-muted-foreground">加载中…</div>}
        {error && <div className="text-[11px] text-bear">{error}</div>}
        {!loading && items.length === 0 && (
          <div className="text-[11px] text-muted-foreground">
            还没有自选基金, 在上方搜索添加。场外基金看净值走势, ETF/LOF 看实时行情与日K。
          </div>
        )}
        {etfItems.length > 0 && (
          <>
            <div className="mb-1 text-[10px] text-muted-foreground">ETF / LOF</div>
            <ul className="mb-1.5 divide-y divide-border/40">
              {etfItems.map((w) => (
                <WatchRow key={w.thscode} item={w} onOpen={setSelected} onRemove={async () => { await fundApi.removeWatch(w.thscode); refresh() }} />
              ))}
            </ul>
          </>
        )}
        {otcItems.length > 0 && (
          <>
            <div className="mb-1 text-[10px] text-muted-foreground">场外基金</div>
            <ul className="divide-y divide-border/40">
              {otcItems.map((w) => (
                <WatchRow key={w.thscode} item={w} onOpen={setSelected} onRemove={async () => { await fundApi.removeWatch(w.thscode); refresh() }} />
              ))}
            </ul>
          </>
        )}
      </Card>

      <p className="text-[10px] text-muted-foreground">
        数据来源: 扶摇开放平台 (免费 Key, 不限累计调用)。场外净值为 T-1 日公布值;
        ETF 日K为前复权口径。场内 ETF 实时行情也会同步到自选股行情轮询。
      </p>
    </div>
  )
}

function WatchRow({
  item,
  onOpen,
  onRemove,
}: {
  item: FundWatchItem
  onOpen: (i: FundWatchItem) => void
  onRemove: () => void
}) {
  const [quote, setQuote] = useState<FundQuote | null>(null)
  const tradable = isTradable(item.kind_label)

  useEffect(() => {
    if (!tradable) return
    let alive = true
    fundApi
      .quote(item.thscode)
      .then((r) => alive && setQuote(r.quote))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [item.thscode, tradable])

  const up = (quote?.change_pct ?? 0) >= 0
  return (
    <li className="flex items-center gap-2 py-1.5">
      <button onClick={() => onOpen(item)} className="flex flex-1 items-center gap-2 text-left">
        <span className="text-[11px] font-medium hover:text-accent">{item.name ?? item.thscode}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{item.thscode}</span>
        {tradable && quote && (
          <>
            <span className="font-mono text-[11px] tabular-nums">{quote.last_price?.toFixed(3) ?? '—'}</span>
            <span className={cn('font-mono text-[11px] tabular-nums', up ? 'text-bull' : 'text-bear')}>
              {pctText(quote.change_pct)}
            </span>
          </>
        )}
      </button>
      <button onClick={onRemove} className="text-[10px] text-muted-foreground hover:text-bear">
        移除
      </button>
    </li>
  )
}
