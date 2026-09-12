import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStocks } from '@/api/stocks'

/**
 * 股票输入：直接敲 6 位代码，或按名称/代码搜库里的票。
 * 库里没有的票也能查——行情按代码取，只是板块、生命周期这些会是 UNKNOWN。
 */
export function StockPicker({ code, name, onPick, inputCls }: {
  code: string
  name: string | null
  onPick: (code: string, name: string | null) => void
  inputCls: string
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const term = q.trim()
  const { data } = useQuery({
    queryKey: ['pretrade-stock-search', term],
    queryFn: () => fetchStocks({ search: term, page_size: 8 }),
    enabled: open && term.length >= 1 && !/^\d{6}$/.test(term),
    staleTime: 60_000,
  })
  const items = data?.items ?? []
  const shown = open ? q : code ? `${code}${name ? ` ${name}` : ''}` : ''

  return (
    <div className="relative">
      <input
        value={shown}
        placeholder="代码或名称，例如 600354 / 敦煌种业"
        className={inputCls}
        onFocus={() => { setOpen(true); setQ('') }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => {
          setQ(e.target.value)
          const v = e.target.value.trim()
          if (/^\d{6}$/.test(v)) onPick(v, null)
        }}
      />
      {open && items.length > 0 && (
        <div className="absolute z-20 mt-1 w-full max-h-64 overflow-auto rounded-lg border border-bg-border bg-bg-elevated shadow-xl shadow-black/40">
          {items.map((s) => (
            <button key={s.code} type="button"
                    onMouseDown={() => { onPick(s.code, s.name); setOpen(false) }}
                    className="flex w-full gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-bg-card">
              <span className="font-mono text-accent">{s.code}</span>
              <span className="text-text-primary">{s.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
