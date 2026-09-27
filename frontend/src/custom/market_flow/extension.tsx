/**
 * 市场数据前端扩展 — 完全解耦模块。
 *
 * 注册 /market 路由 + 侧边栏"市场"导航 + 个股预览底部概念/行业标签,
 * 不修改任何核心文件。删除本目录即整体卸载。apiVersion: 1。
 */
import { useEffect, useState } from 'react'
import { TrendingUp } from 'lucide-react'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'
import { MarketFlowPage } from './MarketFlowPage'
import { marketFlowApi } from './client'

type AnySlotContext = FrontendSlotContextMap[keyof FrontendSlotContextMap]
type StockPreviewContext = FrontendSlotContextMap['stock-preview.footer']

/** 6 位代码 → 带后缀格式 (600900 → 600900.SH) */
function toSuffixed(symbol: string): string {
  const s = symbol.trim()
  if (/^[68]/.test(s)) return `${s}.SH`
  if (/^[03]/.test(s)) return `${s}.SZ`
  if (/^[49]/.test(s)) return `${s}.BJ`
  return s
}

/** 个股预览底部: 概念/行业标签 */
function StockConcepts(props: AnySlotContext) {
  const { symbol } = props as StockPreviewContext
  const [concepts, setConcepts] = useState<string[]>([])
  const [industries, setIndustries] = useState<string[]>([])

  useEffect(() => {
    let alive = true
    const suf = toSuffixed(symbol)
    marketFlowApi.concepts(suf).then((r) => alive && setConcepts(r.concepts)).catch(() => {})
    marketFlowApi.industries(suf).then((r) => alive && setIndustries(r.industries)).catch(() => {})
    return () => {
      alive = false
    }
  }, [symbol])

  if (concepts.length === 0 && industries.length === 0) return null

  return (
    <div className="space-y-1 px-1 pb-1">
      {industries.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">行业:</span>
          {industries.slice(0, 5).map((ind) => (
            <span
              key={ind}
              className="rounded border border-accent/25 bg-accent/10 px-1.5 py-px text-[10px] text-accent"
            >
              {ind}
            </span>
          ))}
        </div>
      )}
      {concepts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-muted-foreground">概念:</span>
          {concepts.slice(0, 8).map((c) => (
            <span
              key={c}
              className="rounded border border-border/40 bg-muted/40 px-1.5 py-px text-[10px] text-foreground"
            >
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

const extension: FrontendExtension = {
  id: 'market.flow',
  apiVersion: 1,
  routes: [{ id: 'market-flow', path: '/market', component: MarketFlowPage }],
  navigation: [
    {
      id: 'market-flow',
      routeId: 'market-flow',
      label: '市场',
      icon: TrendingUp,
      order: 310,
    },
  ],
  slots: [
    {
      name: 'stock-preview.footer',
      id: 'market-flow-concepts',
      order: 100,
      component: StockConcepts,
    },
  ],
}

export default extension
