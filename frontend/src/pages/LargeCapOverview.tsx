import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Landmark, RefreshCw, AlertTriangle, Info } from 'lucide-react'
import {
  fetchLargeCapOverview, fetchLargeCapSyncStatus, triggerLargeCapSync,
  type LargeCapOverviewOk, type LargeCapBatch,
} from '@/api/largeCap'
import { Card } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { GroupTable, type Window } from '@/components/largeCap/GroupTable'
import { StockTable } from '@/components/largeCap/StockTable'
import { ChangeLists, Movers, VolumeSurges } from '@/components/largeCap/ChangeLists'
import { NUM, fmtAmt, fmtCap, fmtPct, pctCls } from '@/components/largeCap/shared'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/utils/cn'

type Kind = 'industry_l1' | 'industry_l2' | 'concept'

const SOURCE_LABEL: Record<string, string> = {
  eastmoney_clist: '东方财富行情列表',
  tencent_quote: '腾讯行情（备用）',
}
const WINDOWS: { key: Window; label: string }[] = [
  { key: 'pct', label: '今日' }, { key: '5d', label: '5D' }, { key: '10d', label: '10D' },
  { key: '20d', label: '20D' }, { key: '60d', label: '60D' },
]
const KINDS: { key: Kind; label: string }[] = [
  { key: 'industry_l1', label: '一级行业' }, { key: 'industry_l2', label: '二级行业' }, { key: 'concept', label: '概念' },
]

function Seg<T extends string>({ options, value, onChange }: {
  options: { key: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded border border-bg-border overflow-hidden">
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)}
                className={cn('px-2 py-0.5 text-xs transition-colors',
                  value === o.key ? 'bg-accent/15 text-accent font-medium' : 'text-text-muted hover:text-text-secondary')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

function Kpi({ label, value, sub, cls, title }: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  cls?: string
  title?: string
}) {
  return (
    <div className="card-elevated px-3 py-2 min-w-0" title={title}>
      <div className="text-[11px] text-text-muted whitespace-nowrap">{label}</div>
      <div className={cn('text-base font-semibold leading-tight', NUM, cls ?? 'text-text-primary')}>{value}</div>
      {sub && <div className="text-[10px] text-text-muted truncate">{sub}</div>}
    </div>
  )
}

const fmtTime = (s: string | null | undefined) => (s ? s.replace('T', ' ').slice(5, 16) : '—')

function statusBadges(d: LargeCapOverviewOk) {
  const out: { text: string; cls: string; title?: string }[] = []
  if (d.batch?.is_settled) out.push({ text: '已收盘', cls: 'bg-up/10 text-up' })
  if (d.date !== d.available_dates[0]) out.push({ text: '历史数据', cls: 'bg-bg-elevated text-text-secondary' })
  if (d.is_stale) out.push({ text: '数据过期', cls: 'bg-warn/15 text-warn', title: `应有 ${d.expected_latest} 的收盘快照` })
  if (d.pending_today) out.push({ text: '今日待同步', cls: 'bg-bg-elevated text-text-muted' })
  if (d.batch && !d.batch.is_complete) out.push({ text: '数据不完整', cls: 'bg-warn/15 text-warn' })
  if (d.batch?.source === 'tencent_quote') out.push({ text: '备用源', cls: 'bg-warn/15 text-warn' })
  const la = d.latest_attempt
  if (la && d.batch && la.id > d.batch.id && !['ok', 'kept_existing'].includes(la.status)) {
    out.push({ text: '最近一次同步没成功', cls: 'bg-warn/15 text-warn',
               title: `${fmtTime(la.finished_at)} ${la.status}：${la.error ?? ''}` })
  }
  return out
}

function SyncButton({ onDone }: { onDone: () => void }) {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const [polling, setPolling] = useState(false)
  const [msg, setMsg] = useState('')
  const st = useQuery({ queryKey: ['large-cap-sync'], queryFn: fetchLargeCapSyncStatus,
                        enabled: polling, refetchInterval: polling ? 3000 : false })
  useEffect(() => {
    if (!polling || !st.data || st.data.status === 'running') return
    setPolling(false)
    setMsg(st.data.status === 'done' ? '同步完成' : `没写入（退出码 ${st.data.exit_code}），见同步记录`)
    onDone()
  }, [polling, st.data]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!isLoggedIn) return null
  const start = async () => {
    setMsg('')
    try {
      await triggerLargeCapSync()
      setPolling(true)
    } catch (e: any) {  // eslint-disable-line @typescript-eslint/no-explicit-any
      setMsg(e?.message ?? '启动失败')
    }
  }
  const last = st.data?.log_lines?.[st.data.log_lines.length - 1]
  return (
    <div className="flex items-center gap-2 text-xs">
      <button onClick={start} disabled={polling}
              title="抓一次全市场收盘快照（约 2~5 分钟）；盘中点了只记批次，不写快照"
              className={cn('inline-flex items-center gap-1 px-2 py-1 rounded border border-bg-border',
                polling ? 'text-text-muted cursor-wait' : 'text-text-secondary hover:text-accent hover:border-accent/40')}>
        <RefreshCw className={cn('w-3.5 h-3.5', polling && 'animate-spin')} /> {polling ? '同步中' : '立即同步'}
      </button>
      {polling && last && <span className="text-text-muted truncate max-w-[22rem]">{last}</span>}
      {!polling && msg && <span className="text-text-muted">{msg}</span>}
    </div>
  )
}

function BatchLine({ b }: { b: LargeCapBatch }) {
  return (
    <span>
      来源 <b className="text-text-secondary">{SOURCE_LABEL[b.source] ?? b.source}</b>
      {' · '}同步于 <span className={NUM}>{fmtTime(b.finished_at ?? b.started_at)}</span>
      {' · '}全市场 <span className={NUM}>{b.observed_count}/{b.expected_count}</span> 只，
      有效总市值 <span className={NUM}>{b.valid_market_cap_count}</span>
    </span>
  )
}

export default function LargeCapOverview() {
  const qc = useQueryClient()
  const [date, setDate] = useState<string | undefined>()
  const [threshold, setThreshold] = useState<number | undefined>()
  const [customOpen, setCustomOpen] = useState(false)
  const [customVal, setCustomVal] = useState('')
  const [win, setWin] = useState<Window | null>(null)
  const [kindSel, setKind] = useState<Kind | null>(null)
  const [conceptScope, setConceptScope] = useState<'watched' | 'all'>('watched')
  // 只有一家的组，中位数就是那一家——按中位数排序时会全挤到最上面。概念默认至少 3 家
  const [minCount, setMinCount] = useState<number | null>(null)
  const [tab, setTab] = useState<'overview' | 'stocks'>('overview')
  const [industryFilter, setIndustryFilter] = useState('')
  const [search, setSearch] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['large-cap-overview', date ?? null, threshold ?? null],
    queryFn: () => fetchLargeCapOverview({ date, threshold }),
    placeholderData: keepPreviousData,
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['large-cap-overview'] })

  const ok: LargeCapOverviewOk | null = data && data.status === 'ok' ? data : null
  const window: Window = win ?? (ok ? (`${ok.config.history_days}d` as Window) : '20d')
  // 一级行业一只都映射不上时（层级表里没有这些二级），默认落到二级行业，不展示一张只有「一级未映射」的表
  const kind: Kind = kindSel ?? (ok && ok.classification.with_industry_l1 === 0 ? 'industry_l2' : 'industry_l1')
  const stocksByCode = useMemo(() => new Map((ok?.stocks ?? []).map((s) => [s.code, s])), [ok])
  const minMembers = minCount ?? (kind === 'concept' ? 3 : 1)
  const scoped = useMemo(() => {
    if (!ok) return []
    const g = ok.groups[kind]
    return kind === 'concept' && conceptScope === 'watched' ? g.filter((x) => x.watched) : g
  }, [ok, kind, conceptScope])
  // 今天没成员、只有跨出的组照样留着（不然跨出的方向就从表里消失了）
  const groups = useMemo(() => scoped.filter((g) => g.count >= minMembers || (g.exited ?? 0) > 0),
    [scoped, minMembers])
  const filteredStocks = useMemo(() => {
    const q = search.trim()
    return (ok?.stocks ?? []).filter((s) => (!industryFilter || s.industry_l1 === industryFilter)
      && (!q || s.code.includes(q) || (s.name ?? '').includes(q) || s.industry_l2.includes(q)))
  }, [ok, industryFilter, search])

  if (isLoading && !data) return <LoadingSpinner />
  if (isError) {
    const detail = (error as any)?.message  // eslint-disable-line @typescript-eslint/no-explicit-any
    return <div className="card p-6 text-sm text-down">大市值数据读取失败：{detail ?? String(error)}</div>
  }
  if (!data) return null

  const thr = data.threshold_yi
  const cfgThr = data.config.threshold_yi
  const presets = data.config.threshold_presets

  const thresholdControl = (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="text-text-muted">市值 ≥</span>
      <select value={customOpen ? 'custom' : String(thr)}
              onChange={(e) => {
                if (e.target.value === 'custom') { setCustomOpen(true); setCustomVal(String(thr)); return }
                setCustomOpen(false)
                const v = Number(e.target.value)
                setThreshold(v === cfgThr ? undefined : v)
              }}
              className="bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 text-text-primary">
        {[...new Set([...presets, cfgThr, thr])].sort((a, b) => a - b).map((p) => (
          <option key={p} value={String(p)}>{p}亿{p === cfgThr ? '（配置）' : ''}</option>
        ))}
        <option value="custom">自定义…</option>
      </select>
      {customOpen && (
        <form onSubmit={(e) => {
          e.preventDefault()
          const v = Number(customVal)
          if (v > 0) { setThreshold(v === cfgThr ? undefined : v); setCustomOpen(false) }
        }} className="flex items-center gap-1">
          <input value={customVal} onChange={(e) => setCustomVal(e.target.value)} inputMode="decimal"
                 className="w-20 bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 text-text-primary" />
          <button className="px-1.5 py-0.5 rounded bg-accent/15 text-accent">确定</button>
        </form>
      )}
    </div>
  )

  if (data.status === 'no_data') {
    return (
      <div className="space-y-3 animate-fade-in">
        <div className="card p-4 flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Landmark className="w-4 h-4 text-accent" />
            <h1 className="text-base font-semibold text-text-primary">大市值概览</h1>
          </div>
          <SyncButton onDone={refresh} />
        </div>
        <div className="card p-6 text-sm text-text-secondary space-y-1">
          <div>{data.message}。每个交易日盘后日更之后会自动同步一次全市场收盘快照，没拿全会在晚上和次日早上重试。</div>
          {data.latest_attempt && (
            <div className="text-xs text-text-muted">
              最近一次尝试 {fmtTime(data.latest_attempt.finished_at ?? data.latest_attempt.started_at)}：
              {data.latest_attempt.status} {data.latest_attempt.error ?? ''}
            </div>
          )}
        </div>
      </div>
    )
  }

  const d = data
  const s = d.summary
  const multipleReady = d.amount_history.available >= d.amount_history.required
  const badges = statusBadges(d)
  const weightText = d.weight_basis === 'prev_cap'
    ? `权重 = 前一交易日（${d.prev_date}）总市值`
    : '权重 = 当日总市值（前一交易日没有快照；这是当前市值权重的截面统计，不是组合收益）'
  const l1Options = [...new Set(d.stocks.map((x) => x.industry_l1))].sort()

  return (
    <div className="space-y-3 animate-fade-in">
      {/* ── 顶部：标题 / 状态 / 筛选 ─────────────────────────────────── */}
      <div className="card p-3 space-y-2">
        <div className="flex items-center gap-x-4 gap-y-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Landmark className="w-4 h-4 text-accent" />
            <h1 className="text-base font-semibold text-text-primary">大市值概览</h1>
            <span className={cn('text-sm text-text-secondary', NUM)}>{d.date}</span>
            {badges.map((b) => (
              <span key={b.text} title={b.title} className={cn('text-[11px] px-1.5 py-0.5 rounded', b.cls)}>{b.text}</span>
            ))}
          </div>
          <div className="flex items-center gap-3 flex-wrap ml-auto">
            {thresholdControl}
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-text-muted">日期</span>
              <select value={d.date} onChange={(e) => setDate(e.target.value === d.available_dates[0] ? undefined : e.target.value)}
                      className="bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 text-text-primary">
                {d.available_dates.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </div>
            <SyncButton onDone={refresh} />
          </div>
        </div>
        <div className="text-[11px] text-text-muted leading-relaxed">
          {d.batch && <BatchLine b={d.batch} />}
          {' · '}统计母体：{d.universe_scope}中总市值 ≥ <b className="text-text-secondary">{fmtCap(thr)}</b> 的公司
          {d.config.include_st ? '（含 ST）' : '（不含 ST）'}
          {thr !== cfgThr && <span className="text-warn">（临时门槛，配置值 {fmtCap(cfgThr)}）</span>}
        </div>
      </div>

      {/* ── KPI ────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
        <Kpi label="池内公司" value={s.count}
             sub={`全市场 ${s.universe_count} 只，市值未知 ${s.unknown_cap}`} />
        <Kpi label="池内总市值" value={fmtCap(s.total_cap)} sub={`成交额 ${fmtAmt(s.amount)}`} />
        <Kpi label="上涨 / 平 / 下跌" value={<><span className="text-up">{s.up}</span><span className="text-text-muted"> / {s.flat} / </span><span className="text-down">{s.down}</span></>}
             sub={`有效 ${s.n_pct}/${s.count}`} />
        <Kpi label="今日中位涨幅" value={fmtPct(s.median_pct)} cls={pctCls(s.median_pct)}
             sub={<>5D <span className={pctCls(s.median_5d)}>{fmtPct(s.median_5d)}</span> · 20D <span className={pctCls(s.median_20d)}>{fmtPct(s.median_20d)}</span></>}
             title="池内公司涨跌幅的中位数，大小公司一视同仁；5D / 20D 是当前成员回看近 N 日的中位数" />
        <Kpi label="今日市值加权" value={fmtPct(s.weighted_pct)} cls={pctCls(s.weighted_pct)}
             sub={`参与 ${s.n_weighted}/${s.count}`} title={weightText} />
        <Kpi label={`跨入 ${fmtCap(thr)}`} value={s.entered ?? '—'} cls={s.entered ? 'text-up' : undefined}
             sub={s.entered == null ? '前一交易日无快照' : `首次观测 ${s.first_observed}`} />
        <Kpi label={`跨出 ${fmtCap(thr)}`} value={s.exited ?? '—'} cls={s.exited ? 'text-down' : undefined}
             sub={s.exited == null ? '前一交易日无快照' : undefined} />
        <Kpi label="放量公司" value={s.surge_multiple ?? s.surge_volume_ratio}
             sub={s.surge_multiple != null ? `成交额 ≥ 20日均×${d.config.surge_amount_multiple}` : `量比 ≥ ${d.config.surge_volume_ratio}`} />
      </div>

      {d.warnings.length > 0 && (
        <div className="card px-3 py-2 border border-warn/30 space-y-0.5">
          {d.warnings.map((w) => (
            <div key={w} className="text-xs text-warn flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}
            </div>
          ))}
        </div>
      )}
      {d.notes.length > 0 && (
        <div className="px-1 space-y-0.5">
          {d.notes.map((n) => (
            <div key={n} className="text-[11px] text-text-muted flex items-start gap-1.5">
              <Info className="w-3 h-3 shrink-0 mt-0.5" />{n}
            </div>
          ))}
        </div>
      )}

      <Seg options={[{ key: 'overview', label: '总览' }, { key: 'stocks', label: `全部公司（${s.count}）` }]}
           value={tab} onChange={setTab} />

      {tab === 'overview' ? (
        <>
          <div className="card p-0 overflow-hidden">
            <div className="px-3 py-2 flex items-center gap-3 flex-wrap border-b border-bg-border">
              <Seg options={KINDS} value={kind} onChange={(k) => { setKind(k); setMinCount(null) }} />
              {kind === 'concept' && (
                <Seg options={[{ key: 'watched', label: '关注概念' }, { key: 'all', label: '全部概念' }]}
                     value={conceptScope} onChange={setConceptScope} />
              )}
              <div className="flex items-center gap-1.5 text-xs" title="成员太少时中位数就是个别公司自己的涨幅">
                <span className="text-text-muted">成员 ≥</span>
                <select value={minMembers} onChange={(e) => setMinCount(Number(e.target.value))}
                        className="bg-bg-elevated border border-bg-border rounded px-1 py-0.5 text-text-primary">
                  {[1, 2, 3, 5, 10].map((n) => <option key={n} value={n}>{n} 家</option>)}
                </select>
                {scoped.length > groups.length && (
                  <span className="text-text-muted">（隐藏 {scoped.length - groups.length} 个）</span>
                )}
              </div>
              <div className="flex items-center gap-1.5 text-xs">
                <span className="text-text-muted">统计窗口</span>
                <Seg options={WINDOWS} value={window} onChange={setWin} />
              </div>
              <span className="text-[11px] text-text-muted ml-auto flex items-center gap-1">
                <Info className="w-3 h-3" />
                {kind === 'concept'
                  ? '概念存在多重归属，各概念的家数和市值不能相加'
                  : '统计的是门槛内的大市值公司，不是行业全部上市公司'}
                ；多日为「当前成员回看近 N 日」的中位数
              </span>
            </div>
            <GroupTable groups={groups} stocksByCode={stocksByCode} window={window}
                        eventsAvailable={d.events.available} multipleReady={multipleReady}
                        label={KINDS.find((k) => k.key === kind)!.label} maxHeight={560}
                        weightBasis={d.weight_basis}
                        emptyText={kind !== 'concept' ? undefined
                          : d.classification.with_concept === 0 ? '这一天的快照没有概念数据'
                          : conceptScope === 'watched' ? `关注概念里没有 ≥${minMembers} 家成员的，可以切到「全部概念」或调低成员数`
                          : `没有 ≥${minMembers} 家成员的概念`} />
            <div className="px-3 py-1.5 text-[11px] text-text-muted border-t border-bg-border">
              {d.classification.note}
              {' '}一级行业映射 {d.classification.with_industry_l1}/{d.classification.members}，
              有概念 {d.classification.with_concept}/{d.classification.members}
            </div>
          </div>

          <ChangeLists events={d.events} threshold={thr} />
          <Movers stocks={d.stocks} />
          <VolumeSurges rows={d.volume_surges} multipleReady={multipleReady}
                        multiple={d.config.surge_amount_multiple} ratio={d.config.surge_volume_ratio} />
        </>
      ) : (
        <Card title={`全部公司（${filteredStocks.length}）`} action={
          <div className="flex items-center gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="名称 / 代码 / 二级行业"
                   className="w-40 bg-bg-elevated border border-bg-border rounded px-2 py-0.5 text-xs text-text-primary placeholder:text-text-muted" />
            <select value={industryFilter} onChange={(e) => setIndustryFilter(e.target.value)}
                    className="bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 text-xs text-text-primary">
              <option value="">全部一级行业</option>
              {l1Options.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </div>
        } className="p-0 [&>div:first-child]:px-3 [&>div:first-child]:pt-3">
          <StockTable rows={filteredStocks} showIndustry maxHeight={720} />
        </Card>
      )}
    </div>
  )
}
