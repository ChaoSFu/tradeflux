import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/utils/cn'

export type SortDir = 'asc' | 'desc'
export interface SortState<K extends string> { key: K | ''; dir: SortDir }

/**
 * 可排序列头。活跃股池那套样式（选中列变 accent + 箭头，未选中悬停才透出双箭头）
 * 原本在 StockPool / LimitUpSectorRadar / SectorConfig 各抄了一份，第四份就该抽了。
 */
export function SortTh<K extends string>({
  col, label, sort, onSort, align = 'right', className, children, rowSpan,
}: {
  col: K
  label?: string
  sort: SortState<K>
  onSort: (k: K) => void
  align?: 'left' | 'right'
  className?: string
  children?: React.ReactNode
  /** 双行表头里跨两行的排序列。**别在外面再包一层 <th>**——它自己渲染的就是 th */
  rowSpan?: number
}) {
  const active = sort.key === col
  return (
    <th
      rowSpan={rowSpan}
      onClick={() => onSort(col)}
      className={cn(
        'px-3 py-2 text-xs font-medium cursor-pointer select-none group whitespace-nowrap',
        align === 'left' ? 'text-left' : 'text-right',
        active ? 'text-accent' : 'text-text-secondary/55 hover:text-text-secondary',
        className,
      )}
    >
      <span className={cn('inline-flex items-center gap-0.5',
        align === 'left' ? 'justify-start' : 'justify-end')}>
        {children ?? label}
        {active
          ? (sort.dir === 'desc'
              ? <ChevronDown className="w-3 h-3 shrink-0" />
              : <ChevronUp className="w-3 h-3 shrink-0" />)
          : <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-0
                                       group-hover:opacity-40 transition-opacity" />}
      </span>
    </th>
  )
}

/**
 * 排序比较器。**null 永远沉底,不管升序降序**——「不知道」不是「最小」,
 * 让它跟 -8.52% 抢倒数第一是把空值当成了事实。
 */
export function compareWithNullsLast(
  a: number | string | null | undefined,
  b: number | string | null | undefined,
  dir: SortDir,
): number {
  const an = a === null || a === undefined
  const bn = b === null || b === undefined
  if (an && bn) return 0
  if (an) return 1
  if (bn) return -1
  const c = typeof a === 'string' || typeof b === 'string'
    ? String(a).localeCompare(String(b))
    : (a as number) - (b as number)
  return dir === 'desc' ? -c : c
}
