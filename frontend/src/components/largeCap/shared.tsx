import { createContext, useContext, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'
import { cn } from '@/utils/cn'

export const NUM = 'font-mono tabular-nums'

/**
 * 快照那天怎么称呼。是今天就叫「今日」；不是（今天的收盘快照还没同步、或在看历史）就写日期——
 * 2026-11-11 15:17 页面写着「今日 +6.29%」，其实是 11-10 的收盘，用户以为今天的数据没更新。
 */
export interface DayLabels { day: string; prev: string; isToday: boolean }
const TODAY_LABELS: DayLabels = { day: '今日', prev: '昨日', isToday: true }
export const DayLabelContext = createContext<DayLabels>(TODAY_LABELS)
export const useDayLabels = () => useContext(DayLabelContext)
export const shanghaiToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
export function dayLabelsOf(date: string, prevDate: string | null | undefined, today = shanghaiToday()): DayLabels {
  if (date === today) return TODAY_LABELS
  return { day: date.slice(5), prev: prevDate ? prevDate.slice(5) : '前一日', isToday: false }
}

// null = 没有这个数据，一律显示 —，不显示成 0
export const fmtPct = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
export const pctCls = (v: number | null | undefined) =>
  v == null ? 'text-text-muted/50' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary'
/** 市值（亿元） */
export const fmtCap = (v: number | null | undefined) =>
  v == null ? '—' : v >= 10000 ? `${(v / 10000).toFixed(2)}万亿` : `${v.toFixed(0)}亿`
/** 成交额（亿元） */
export const fmtAmt = (v: number | null | undefined) =>
  v == null ? '—' : v >= 1 ? `${v.toFixed(1)}亿` : `${(v * 1e4).toFixed(0)}万`
export const fmtNum = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))

export type SortDir = 'asc' | 'desc'

/** 缺值排最后，不当 0 参与排序 */
export function sortRows<T>(rows: T[], get: (r: T) => number | null | undefined, dir: SortDir): T[] {
  const has = rows.filter((r) => get(r) != null)
  const none = rows.filter((r) => get(r) == null)
  has.sort((a, b) => (dir === 'desc' ? (get(b) as number) - (get(a) as number) : (get(a) as number) - (get(b) as number)))
  return [...has, ...none]
}

export function useSort<K extends string>(initial: K) {
  const [key, setKey] = useState<K>(initial)
  const [dir, setDir] = useState<SortDir>('desc')
  const onSort = (k: K) => {
    if (k === key) setDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setKey(k); setDir('desc') }
  }
  return { key, dir, onSort, setKey }
}

export function SortTh<K extends string>({ label, col, sort, title, className }: {
  label: string
  col: K
  sort: { key: K; dir: SortDir; onSort: (k: K) => void }
  title?: string
  className?: string
}) {
  const active = sort.key === col
  return (
    <th className={cn('px-2 py-1.5 text-right font-medium whitespace-nowrap select-none', className)} title={title}>
      <button onClick={() => sort.onSort(col)}
              className={cn('inline-flex items-center gap-0.5 hover:text-text-secondary',
                active ? 'text-accent' : 'text-text-muted')}>
        {label}
        {active && (sort.dir === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />)}
      </button>
    </th>
  )
}

const emQuoteUrl = (code: string) => `https://quote.eastmoney.com/${code.startsWith('6') ? 'sh' : 'sz'}${code}.html`

/**
 * 有个股详情就进详情页；没有的（本系统只收录进过候选池的股票）开东财行情页，
 * 不让人点进一个「未找到该股票」
 */
export function StockLink({ code, name, hasDetail = true }: { code: string; name: string | null; hasDetail?: boolean }) {
  const inner = (
    <>
      <span className="text-text-primary">{name || code}</span>
      <span className={cn('ml-1 text-[10px] text-text-muted', NUM)}>{code}</span>
    </>
  )
  if (hasDetail) return <Link to={`/stocks/${code}`} className="hover:text-accent">{inner}</Link>
  return (
    <a href={emQuoteUrl(code)} target="_blank" rel="noopener noreferrer" className="hover:text-accent"
       title="本系统的个股详情只收录进过候选池的股票，这只没有；点开东方财富行情页">
      {inner}<ExternalLink className="inline w-2.5 h-2.5 ml-0.5 text-text-muted align-baseline" />
    </a>
  )
}

export function UpFlatDown({ up, flat, down }: { up: number; flat: number; down: number }) {
  return (
    <span className={cn(NUM, 'whitespace-nowrap')}>
      <span className="text-up">{up}</span>
      <span className="text-text-muted">/{flat}/</span>
      <span className="text-down">{down}</span>
    </span>
  )
}
