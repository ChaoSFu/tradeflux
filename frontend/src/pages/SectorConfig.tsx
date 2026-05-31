/**
 * 板块展示管理页
 * 控制哪些板块以 tag 形式展示在强势股池中。
 * is_watched=true → 展示；false → 隐藏。
 */
import { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import client from '@/api/client'
import { Search, Eye, EyeOff, X, RefreshCw, Loader2, ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/utils/cn'

// ─── Types ────────────────────────────────────────────────────────────────────

interface SectorItem {
  id: number
  code: string
  name: string
  sector_type: string | null
  is_watched: boolean
  stock_count: number | null
  total_market_cap: number | null
  amount: number | null
  turnover_rate: number | null
  pct_change_30d: number | null   // 今日涨幅 %
  pct_change_5d: number | null    // 近5日涨幅 %
  pct_change_10d: number | null   // 近10日涨幅 %
}

interface SyncStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  message: string
  log_lines: string[]
}

// ─── API helpers ──────────────────────────────────────────────────────────────

const fetchSectorConfig = (): Promise<SectorItem[]> =>
  client.get('/admin/sectors').then((r) => r.data)

const toggleWatch = (id: number): Promise<{ id: number; name: string; is_watched: boolean }> =>
  client.patch(`/admin/sectors/${id}/watch`).then((r) => r.data)

const batchWatch = (ids: number[], watched: boolean) =>
  client.post('/admin/sectors/batch-watch', ids, { params: { watched } }).then((r) => r.data)

const triggerSyncBoards = () =>
  client.post('/admin/sync-boards').then((r) => r.data)

const fetchSyncBoardsStatus = (): Promise<SyncStatus> =>
  client.get('/admin/sync-boards/status').then((r) => r.data)

// ─── Page ─────────────────────────────────────────────────────────────────────

const TYPE_LABELS: Record<string, string> = {
  concept:     '概念',
  industry:    '行业',
  region:      '地区',
  industry_l3: '行业三级',  // legacy, from old sync
  em_industry: '东财行业',  // legacy, from old sync
}

// Tab order — only tabs with data are rendered
const TYPE_FILTER_ORDER = ['concept', 'industry', 'region', 'industry_l3'] as const

type TypeFilter = 'all' | typeof TYPE_FILTER_ORDER[number]
type SortCol = 'name' | 'stock_count' | 'total_market_cap' | 'amount' | 'turnover_rate' | 'pct_change_30d' | 'pct_change_5d' | 'pct_change_10d'
type SortDir = 'asc' | 'desc'

// ─── Percent cell (green/red coloring) ───────────────────────────────────────

function PctCell({ value }: { value: number | null }) {
  if (value == null || value === 0) {
    return <td className="px-2 py-2.5 text-right font-mono text-xs text-text-muted whitespace-nowrap">—</td>
  }
  return (
    <td className={cn(
      'px-2 py-2.5 text-right font-mono text-xs whitespace-nowrap',
      value > 0 ? 'text-up' : 'text-down',
    )}>
      {value > 0 ? '+' : ''}{value.toFixed(2)}%
    </td>
  )
}

// ─── Sort column header ────────────────────────────────────────────────────────

function SortTh({
  col, label, align = 'right', className = '',
  sortCol, sortDir, onSort,
}: {
  col: SortCol
  label: string
  align?: 'left' | 'right' | 'center'
  className?: string
  sortCol: SortCol
  sortDir: SortDir
  onSort: (col: SortCol) => void
}) {
  const active = sortCol === col
  return (
    <th
      onClick={() => onSort(col)}
      className={cn(
        'px-2 py-2 text-xs font-medium cursor-pointer select-none group whitespace-nowrap',
        align === 'left' ? 'text-left' : align === 'right' ? 'text-right' : 'text-center',
        active ? 'text-accent' : 'text-text-muted hover:text-text-secondary',
        className,
      )}
    >
      <span className={cn('inline-flex items-center gap-0.5', align === 'right' && 'justify-end')}>
        {label}
        {active
          ? sortDir === 'desc'
            ? <ChevronDown className="w-3 h-3 shrink-0" />
            : <ChevronUp className="w-3 h-3 shrink-0" />
          : <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-0 group-hover:opacity-40 transition-opacity" />
        }
      </span>
    </th>
  )
}

export default function SectorConfig() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [watchFilter, setWatchFilter] = useState<'all' | 'on' | 'off'>('on')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [sortCol, setSortCol] = useState<SortCol>('stock_count')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [capFilterOn, setCapFilterOn] = useState(false)     // 市值 < threshold 过滤
  const [capThreshold, setCapThreshold] = useState(5000)    // 亿
  const [countFilterOn, setCountFilterOn] = useState(false) // 成份股 < threshold 过滤
  const [countThreshold, setCountThreshold] = useState(50)  // 只
  const [showSyncLog, setShowSyncLog] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  // Board sync status polling (put first so we can use it below)
  const { data: syncStatus } = useQuery({
    queryKey: ['sync-boards-status'],
    queryFn: fetchSyncBoardsStatus,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' ? 3000 : false
    },
  })

  const { data = [], isLoading } = useQuery({
    queryKey: ['admin-sectors'],
    queryFn: fetchSectorConfig,
    staleTime: 10_000,
    // While syncing, refresh every 10s so new board types appear as tabs in real time
    refetchInterval: syncStatus?.status === 'running' ? 10_000 : false,
  })

  const syncMut = useMutation({
    mutationFn: triggerSyncBoards,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sync-boards-status'] })
      setShowSyncLog(true)
    },
  })

  // Auto-scroll log to bottom
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [syncStatus?.log_lines])

  // Refresh sector list when sync completes
  useEffect(() => {
    if (syncStatus?.status === 'done') {
      qc.invalidateQueries({ queryKey: ['admin-sectors'] })
    }
  }, [syncStatus?.status, qc])

  const toggleMut = useMutation({
    mutationFn: toggleWatch,
    onSuccess: (updated) => {
      qc.setQueryData<SectorItem[]>(['admin-sectors'], (prev = []) =>
        prev.map((s) => (s.id === updated.id ? { ...s, is_watched: updated.is_watched } : s)),
      )
    },
  })

  const batchMut = useMutation({
    mutationFn: ({ ids, watched }: { ids: number[]; watched: boolean }) =>
      batchWatch(ids, watched),
    onSuccess: (_res, { ids, watched }) => {
      qc.setQueryData<SectorItem[]>(['admin-sectors'], (prev = []) =>
        prev.map((s) => (ids.includes(s.id) ? { ...s, is_watched: watched } : s)),
      )
      setSelected(new Set())
    },
  })

  // Count per type (for tab badges)
  const typeCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const s of data) {
      const t = s.sector_type ?? 'unknown'
      m[t] = (m[t] ?? 0) + 1
    }
    return m
  }, [data])

  // Only show tabs for types that have at least 1 board in DB
  const activeTypeFilters = TYPE_FILTER_ORDER.filter((t) => (typeCounts[t] ?? 0) > 0)

  const handleSort = (col: SortCol) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  // Filtered + sorted list
  const filtered = useMemo(() => {
    let list = data
    if (typeFilter !== 'all') list = list.filter((s) => s.sector_type === typeFilter)
    if (watchFilter === 'on') list = list.filter((s) => s.is_watched)
    if (watchFilter === 'off') list = list.filter((s) => !s.is_watched)
    if (capFilterOn) list = list.filter((s) => (s.total_market_cap ?? 0) < capThreshold)
    if (countFilterOn) list = list.filter((s) => (s.stock_count ?? 0) < countThreshold)
    if (search.trim()) list = list.filter((s) => s.name.includes(search.trim()))

    list = [...list].sort((a, b) => {
      let av: number | string
      let bv: number | string
      if (sortCol === 'name')              { av = a.name;                     bv = b.name }
      else if (sortCol === 'stock_count')  { av = a.stock_count ?? -Infinity; bv = b.stock_count ?? -Infinity }
      else if (sortCol === 'total_market_cap') { av = a.total_market_cap ?? -Infinity; bv = b.total_market_cap ?? -Infinity }
      else if (sortCol === 'amount')       { av = a.amount ?? -Infinity;      bv = b.amount ?? -Infinity }
      else if (sortCol === 'turnover_rate'){ av = a.turnover_rate ?? -Infinity; bv = b.turnover_rate ?? -Infinity }
      else if (sortCol === 'pct_change_30d') { av = a.pct_change_30d ?? -Infinity; bv = b.pct_change_30d ?? -Infinity }
      else if (sortCol === 'pct_change_5d')  { av = a.pct_change_5d ?? -Infinity;  bv = b.pct_change_5d ?? -Infinity }
      else                                 { av = a.pct_change_10d ?? -Infinity; bv = b.pct_change_10d ?? -Infinity }

      if (typeof av === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av)
      }
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })

    return list
  }, [data, typeFilter, watchFilter, capFilterOn, capThreshold, countFilterOn, countThreshold, search, sortCol, sortDir])

  const watchedCount = data.filter((s) => s.is_watched).length
  const filteredIds = filtered.map((s) => s.id)
  const allFilteredSelected = filteredIds.length > 0 && filteredIds.every((id) => selected.has(id))

  const toggleSelect = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const toggleSelectAll = () => {
    if (allFilteredSelected) {
      setSelected((prev) => { const n = new Set(prev); filteredIds.forEach((id) => n.delete(id)); return n })
    } else {
      setSelected((prev) => { const n = new Set(prev); filteredIds.forEach((id) => n.add(id)); return n })
    }
  }

  const isSyncing = syncStatus?.status === 'running' || syncMut.isPending

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Sync panel */}
      <div className="p-3 rounded-lg bg-bg-card border border-bg-border">
        <div className="flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-text-primary">东财板块全量同步</div>
            <div className="text-xs text-text-muted mt-0.5">
              概念(~399) + 行业全量(~457, WAP行业标签) + 地区(~31)，新板块默认隐藏，预计 5–8 分钟
            </div>
          </div>
          {syncStatus && syncStatus.status !== 'idle' && (
            <button
              onClick={() => setShowSyncLog((v) => !v)}
              className="text-xs text-text-muted hover:text-text-primary transition-colors underline"
            >
              {showSyncLog ? '收起日志' : '查看日志'}
            </button>
          )}
          <button
            onClick={() => { syncMut.mutate(); setShowSyncLog(true) }}
            disabled={isSyncing}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border transition-all shrink-0',
              isSyncing
                ? 'bg-accent/5 text-accent/50 border-accent/20 cursor-not-allowed'
                : 'bg-accent/10 text-accent border-accent/30 hover:bg-accent/20',
            )}
          >
            {isSyncing
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> 同步中...</>
              : <><RefreshCw className="w-3.5 h-3.5" /> 立即同步</>
            }
          </button>
        </div>

        {/* Status row */}
        {syncStatus && syncStatus.status !== 'idle' && (
          <div className={cn(
            'mt-2 pt-2 border-t border-bg-border/40 text-xs flex items-center gap-2',
            syncStatus.status === 'done' ? 'text-down' :
            syncStatus.status === 'error' ? 'text-up' : 'text-text-muted'
          )}>
            {syncStatus.status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
            <span>{syncStatus.message}</span>
            {syncStatus.started_at && (
              <span className="ml-auto text-text-muted/85">
                开始: {syncStatus.started_at}
                {syncStatus.finished_at && ` → ${syncStatus.finished_at}`}
              </span>
            )}
          </div>
        )}

        {/* Log panel */}
        {showSyncLog && syncStatus && syncStatus.log_lines.length > 0 && (
          <div
            ref={logRef}
            className="mt-2 bg-bg-base rounded p-2 max-h-48 overflow-y-auto font-mono text-xs text-text-muted leading-relaxed"
          >
            {syncStatus.log_lines.map((line, i) => (
              <div key={i} className={cn(
                line.includes('✅') || line.includes('★') ? 'text-down' :
                line.includes('❌') ? 'text-up' :
                line.includes('[新]') ? 'text-accent' : ''
              )}>
                {line}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Summary bar */}
      <div className="flex items-center gap-3 p-3 rounded-lg bg-bg-card border border-bg-border text-sm">
        <Eye className="w-4 h-4 text-accent shrink-0" />
        <span className="text-text-secondary">
          共 <span className="text-text-primary font-mono font-semibold">{data.length}</span> 个板块，
          <span className="text-accent font-mono font-semibold"> {watchedCount}</span> 个展示中，
          <span className="text-text-muted font-mono"> {data.length - watchedCount}</span> 个已隐藏
        </span>
        <span className="ml-auto text-xs text-text-muted">
          修改即时生效，同步不会覆盖已有配置
        </span>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索板块名..."
            className="bg-bg-card border border-bg-border rounded pl-8 pr-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent/50 w-44"
          />
        </div>

        {/* Type filter — only show tabs for types that exist in DB */}
        <div className="flex rounded border border-bg-border overflow-hidden text-xs">
          {/* 全部 tab always shown */}
          <button
            onClick={() => setTypeFilter('all')}
            className={cn(
              'px-3 py-1.5 transition-colors',
              typeFilter === 'all'
                ? 'bg-accent/15 text-accent'
                : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
            )}
          >
            全部
          </button>
          {activeTypeFilters.map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={cn(
                'flex items-center gap-1 px-3 py-1.5 transition-colors',
                typeFilter === t
                  ? 'bg-accent/15 text-accent'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
              )}
            >
              {TYPE_LABELS[t]}
              <span className={cn(
                'text-[10px] font-mono leading-none',
                typeFilter === t ? 'text-accent/70' : 'text-text-muted/80'
              )}>
                {typeCounts[t]}
              </span>
            </button>
          ))}
        </div>

        {/* Watch filter */}
        <div className="flex rounded border border-bg-border overflow-hidden text-xs">
          {([['all', '全部'], ['on', '展示中'], ['off', '已隐藏']] as const).map(([v, label]) => (
            <button
              key={v}
              onClick={() => setWatchFilter(v)}
              className={cn(
                'px-3 py-1.5 transition-colors',
                watchFilter === v
                  ? 'bg-accent/15 text-accent'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Market cap threshold filter */}
        <div className={cn(
          'flex items-center rounded border text-xs overflow-hidden transition-colors',
          capFilterOn ? 'border-warn/40' : 'border-bg-border',
        )}>
          <button
            onClick={() => setCapFilterOn((v) => !v)}
            className={cn(
              'px-2.5 py-1.5 whitespace-nowrap transition-colors',
              capFilterOn
                ? 'bg-warn/10 text-warn'
                : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
            )}
          >
            市值&lt;
          </button>
          <input
            type="number"
            value={capThreshold}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10)
              if (!isNaN(v) && v > 0) setCapThreshold(v)
            }}
            className={cn(
              'w-16 py-1.5 text-center bg-transparent border-l border-bg-border/60 font-mono focus:outline-none',
              capFilterOn ? 'text-warn' : 'text-text-muted',
            )}
          />
          <span className={cn(
            'px-1.5 py-1.5 whitespace-nowrap',
            capFilterOn ? 'text-warn/70' : 'text-text-muted/85',
          )}>亿</span>
        </div>

        {/* Stock count threshold filter */}
        <div className={cn(
          'flex items-center rounded border text-xs overflow-hidden transition-colors',
          countFilterOn ? 'border-warn/40' : 'border-bg-border',
        )}>
          <button
            onClick={() => setCountFilterOn((v) => !v)}
            className={cn(
              'px-2.5 py-1.5 whitespace-nowrap transition-colors',
              countFilterOn
                ? 'bg-warn/10 text-warn'
                : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
            )}
          >
            成份股&lt;
          </button>
          <input
            type="number"
            value={countThreshold}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10)
              if (!isNaN(v) && v > 0) setCountThreshold(v)
            }}
            className={cn(
              'w-14 py-1.5 text-center bg-transparent border-l border-bg-border/60 font-mono focus:outline-none',
              countFilterOn ? 'text-warn' : 'text-text-muted',
            )}
          />
          <span className={cn(
            'px-1.5 py-1.5 whitespace-nowrap',
            countFilterOn ? 'text-warn/70' : 'text-text-muted/85',
          )}>只</span>
        </div>

        {/* Batch ops (visible when items selected) */}
        {selected.size > 0 && (
          <div className="flex items-center gap-2 ml-2 pl-2 border-l border-bg-border">
            <span className="text-xs text-text-muted">已选 {selected.size} 个</span>
            <button
              onClick={() => batchMut.mutate({ ids: [...selected], watched: true })}
              disabled={batchMut.isPending}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20 transition-colors disabled:opacity-50"
            >
              <Eye className="w-3.5 h-3.5" /> 批量展示
            </button>
            <button
              onClick={() => batchMut.mutate({ ids: [...selected], watched: false })}
              disabled={batchMut.isPending}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-bg-elevated text-text-muted border border-bg-border hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <EyeOff className="w-3.5 h-3.5" /> 批量隐藏
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="p-1 rounded text-text-muted hover:text-text-primary"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <div className="ml-auto text-xs text-text-muted">
          显示 {filtered.length} / {data.length}
        </div>
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-bg-card border-b border-bg-border/60">
            <tr>
              <th className="px-3 py-2 w-8">
                <input
                  type="checkbox"
                  checked={allFilteredSelected}
                  onChange={toggleSelectAll}
                  className="w-3.5 h-3.5 accent-accent cursor-pointer"
                />
              </th>
              <SortTh col="name" label="板块名称" align="left" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <th className="text-center px-2 py-2 text-xs text-text-muted font-medium w-16 whitespace-nowrap">类型</th>
              <SortTh col="stock_count"     label="成份股"    align="right" className="w-14" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="total_market_cap" label="市值(亿)"  align="right" className="w-18" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="amount"          label="成交额(亿)" align="right" className="w-18" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="turnover_rate"   label="换手率"    align="right" className="w-14" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_30d"  label="今日%"     align="right" className="w-14" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_5d"   label="近5日%"    align="right" className="w-14" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_10d"  label="近10日%"   align="right" className="w-16" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <th className="text-center px-2 py-2 text-xs text-text-muted font-medium w-20 whitespace-nowrap">展示状态</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={11} className="py-12 text-center text-text-muted text-sm">加载中...</td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={11} className="py-12 text-center text-text-muted text-sm">无匹配板块</td>
              </tr>
            ) : (
              filtered.map((sector) => (
                <tr
                  key={sector.id}
                  className={cn(
                    'border-b border-bg-border/25 last:border-0 transition-colors',
                    selected.has(sector.id) ? 'bg-accent/5' : 'hover:bg-bg-elevated',
                  )}
                >
                  {/* Checkbox */}
                  <td className="px-2 py-2.5">
                    <input
                      type="checkbox"
                      checked={selected.has(sector.id)}
                      onChange={() => toggleSelect(sector.id)}
                      className="w-3.5 h-3.5 accent-accent cursor-pointer"
                    />
                  </td>

                  {/* Name */}
                  <td className="px-2 py-2.5">
                    <span className={cn(
                      'text-sm font-medium',
                      sector.is_watched ? 'text-text-primary' : 'text-text-muted line-through',
                    )}>
                      {sector.name}
                    </span>
                    <span className="ml-2 text-xs text-text-muted/85 font-mono">{sector.code}</span>
                  </td>

                  {/* Type */}
                  <td className="px-2 py-2.5 text-center">
                    <span className={cn(
                      'text-xs px-1.5 py-0.5 rounded',
                      sector.sector_type === 'concept'
                        ? 'bg-accent/10 text-accent'
                        : sector.sector_type === 'industry' || sector.sector_type === 'industry_l3'
                        ? 'bg-dragon/10 text-dragon'
                        : sector.sector_type === 'region'
                        ? 'bg-text-muted/10 text-text-secondary'
                        : 'bg-text-muted/10 text-text-muted',
                    )}>
                      {TYPE_LABELS[sector.sector_type ?? ''] ?? sector.sector_type}
                    </span>
                  </td>

                  {/* Stock count */}
                  <td className="px-2 py-2.5 text-right font-mono text-xs text-text-secondary whitespace-nowrap">
                    {sector.stock_count ?? '—'}
                  </td>

                  {/* Market cap */}
                  <td className="px-2 py-2.5 text-right font-mono text-xs text-text-secondary whitespace-nowrap">
                    {sector.total_market_cap != null && sector.total_market_cap > 0
                      ? sector.total_market_cap >= 10000
                        ? `${(sector.total_market_cap / 10000).toFixed(1)}万`
                        : sector.total_market_cap.toFixed(0)
                      : '—'}
                  </td>

                  {/* Amount */}
                  <td className="px-2 py-2.5 text-right font-mono text-xs text-text-secondary whitespace-nowrap">
                    {sector.amount != null ? sector.amount.toFixed(0) : '—'}
                  </td>

                  {/* Turnover */}
                  <td className="px-2 py-2.5 text-right font-mono text-xs text-text-secondary whitespace-nowrap">
                    {sector.turnover_rate != null ? `${sector.turnover_rate.toFixed(1)}%` : '—'}
                  </td>

                  {/* Today % */}
                  <PctCell value={sector.pct_change_30d} />
                  {/* 5d % */}
                  <PctCell value={sector.pct_change_5d} />
                  {/* 10d % */}
                  <PctCell value={sector.pct_change_10d} />

                  {/* Toggle */}
                  <td className="px-2 py-2.5 text-center">
                    <button
                      onClick={() => toggleMut.mutate(sector.id)}
                      disabled={toggleMut.isPending}
                      className={cn(
                        'inline-flex items-center gap-1 mx-auto px-2 py-1 rounded text-xs font-medium border transition-all whitespace-nowrap',
                        sector.is_watched
                          ? 'bg-accent/10 text-accent border-accent/30 hover:bg-down/10 hover:text-down hover:border-down/30'
                          : 'bg-bg-elevated text-text-muted border-bg-border hover:bg-accent/10 hover:text-accent hover:border-accent/30',
                      )}
                    >
                      {sector.is_watched
                        ? <><Eye className="w-3 h-3 shrink-0" /> 展示中</>
                        : <><EyeOff className="w-3 h-3 shrink-0" /> 已隐藏</>
                      }
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
