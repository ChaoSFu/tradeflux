import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStocks } from '@/api/stocks'
import { cn } from '@/utils/cn'

/**
 * 股票输入：敲代码或名称搜库里的票，点结果（或 ↑↓ + 回车）选中。
 * 直接敲满 6 位代码也算选中——库里没有的票也能用（买入检查行情按代码取），库里有的顺手把名称带上。
 *
 * 用在：买入检查、每日复盘「新增关联」（2026-11-22 从 preTrade/ 挪过来共用）。
 */
export function StockPicker({ code, name, onPick, inputCls, placeholder = '代码或名称，例如 600354 / 敦煌种业' }: {
  code: string
  name: string | null
  onPick: (code: string, name: string | null) => void
  inputCls: string
  placeholder?: string
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const term = q.trim()
  const isCode = /^\d{6}$/.test(term)
  const { data, isFetching } = useQuery({
    queryKey: ['stock-picker-search', term],
    queryFn: () => fetchStocks({ search: term, page_size: 8 }),
    enabled: open && term.length >= 1,
    staleTime: 60_000,
  })
  const items = term ? data?.items ?? [] : []
  const shown = open ? q : code ? `${code}${name ? ` ${name}` : ''}` : ''

  useEffect(() => setActive(0), [term])
  // 敲满 6 位代码、库里正好有这只：把名称补上（不用再点一下）
  useEffect(() => {
    if (!isCode) return
    const hit = data?.items.find((s) => s.code === term)
    if (hit) onPick(hit.code, hit.name)
  }, [isCode, term, data]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (c: string, n: string | null) => { onPick(c, n); setOpen(false) }

  return (
    <div className="relative">
      <input
        value={shown}
        placeholder={placeholder}
        className={inputCls}
        onFocus={() => { setOpen(true); setQ('') }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => {
          setQ(e.target.value)
          const v = e.target.value.trim()
          if (/^\d{6}$/.test(v)) onPick(v, null)
        }}
        onKeyDown={(e) => {
          if (!open || !items.length) return
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => (i + 1) % items.length) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => (i - 1 + items.length) % items.length) }
          // 下拉开着时回车是「选这一条」，别顺带提交外面的表单
          else if (e.key === 'Enter') { e.preventDefault(); const s = items[Math.min(active, items.length - 1)]; pick(s.code, s.name) }
          else if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && !!term && (items.length > 0 || (!isFetching && !isCode)) && (
        <div className="absolute z-20 mt-1 w-full min-w-[12rem] max-h-64 overflow-auto rounded-lg border border-bg-border bg-bg-elevated shadow-xl shadow-black/40">
          {items.map((s, i) => (
            <button key={s.code} type="button"
                    onMouseDown={() => pick(s.code, s.name)}
                    onMouseEnter={() => setActive(i)}
                    className={cn('flex w-full gap-2 px-2.5 py-1.5 text-left text-sm', i === active ? 'bg-bg-card' : 'hover:bg-bg-card')}>
              <span className="font-mono text-accent">{s.code}</span>
              <span className="text-text-primary">{s.name}</span>
            </button>
          ))}
          {!items.length && <div className="px-2.5 py-1.5 text-xs text-text-muted">没搜到「{term}」，换个字或直接敲 6 位代码</div>}
        </div>
      )}
    </div>
  )
}
