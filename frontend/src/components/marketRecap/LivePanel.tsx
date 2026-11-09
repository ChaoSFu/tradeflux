import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Radio, RefreshCw } from 'lucide-react'
import { fetchLive, type LiveData, type LiveEvent, type LiveFact, type LiveMeta, type SystemResponse } from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { ATTR_LABEL, NUM, POSTURE_LABEL, Section, StockLink, fmtPct, pctCls } from '@/components/marketRecap/shared'
import { cn } from '@/utils/cn'

const PHASE_TONE: Record<string, string> = {
  trading: 'bg-up/15 text-up', auction: 'bg-accent/15 text-accent', auction_done: 'bg-accent/15 text-accent',
  closing_auction: 'bg-accent/15 text-accent', lunch: 'bg-warn/15 text-warn', closed: 'bg-bg-elevated text-text-secondary',
  pre_open: 'bg-bg-elevated text-text-secondary', non_trading: 'bg-bg-elevated text-text-muted',
  stale: 'bg-warn/15 text-warn', unknown: 'bg-down/15 text-down',
}
const EVENT_TONE: Record<string, string> = {
  limit_up: 'text-up border-up/40', relimit: 'text-up border-up/60', streak_continue: 'text-up border-up/40',
  streak_break: 'text-down border-down/40', broken: 'text-warn border-warn/40', limit_down: 'text-down border-down/60',
}
const SYS_TONE: Record<SystemResponse, string> = {
  limit_up: 'text-up', up_response: 'text-up', independent_strong: 'text-accent', not_met: 'text-text-muted', insufficient: 'text-warn',
}
const STATUS_ZH: Record<LiveFact['status'], string> = { ok: '', missing: '没行情', stale: '非今日', no_trade: '未成交' }
const yi = (v: number | null | undefined) => (v == null ? '—' : `${(v / 1e8).toFixed(0)}亿`)
const hms = (iso: string | null | undefined) => (iso ? iso.slice(11, 19) : '—')
const TH = 'px-2 py-1.5 font-medium whitespace-nowrap'
const TD = 'px-2 py-1 whitespace-nowrap'

/** 临时标：虚线框，跟收盘事件（实心底）一眼分开 */
function Provisional({ className }: { className?: string }) {
  return <span className={cn('text-[10px] px-1 rounded border border-dashed border-warn/50 text-warn whitespace-nowrap', className)}
               title="盘中此刻的状态，收盘前随时会变；不进活跃事件表，正式结论以盘后同步为准">盘中临时</span>
}

function EventChips({ events, labels }: { events: LiveEvent[]; labels: Record<string, string> }) {
  if (!events.length) return null
  return (
    <span className="inline-flex flex-wrap gap-1">
      {events.map((e) => (
        <span key={e.event_type} title={`${e.evidence}（盘中临时）`}
              className={cn('px-1 rounded border border-dashed text-[10px] whitespace-nowrap', EVENT_TONE[e.event_type] ?? 'text-text-secondary border-bg-border')}>
          {labels[e.event_type] ?? e.event_type}
        </span>
      ))}
    </span>
  )
}

/** 现涨跌：没行情 / 非今日 / 未成交 写清楚，不显示成 0 */
function Pct({ f }: { f?: LiveFact | null }) {
  if (!f) return <span className="text-text-muted/50">—</span>
  if (f.status !== 'ok') return <span className="text-[11px] text-warn" title={f.note ?? ''}>{STATUS_ZH[f.status]}</span>
  return <span className={cn(NUM, pctCls(f.pct))}>{fmtPct(f.pct)}</span>
}

function LimitState({ f }: { f?: LiveFact | null }) {
  if (!f || f.status !== 'ok' || !f.limit) return <span className="text-text-muted/50">—</span>
  const [txt, cls] = f.limit === 'sealed' ? ['封板', 'text-up'] : f.limit === 'broken' ? ['炸板', 'text-warn'] : ['跌停', 'text-down']
  return (
    <span className={cn('text-[11px]', cls)}>
      {txt}{f.first_limit_time && <span className={cn('ml-1 text-text-muted', NUM)} title="首次封板时间（东财实时涨停 / 炸板池）">{f.first_limit_time.slice(0, 5)}</span>}
    </span>
  )
}

function MetaNote({ meta }: { meta?: LiveMeta }) {
  if (!meta || (meta.status === 'ok' && !meta.note)) return null
  return <div className={cn('text-[10px] leading-snug', meta.status === 'ok' ? 'text-text-muted' : 'text-warn')} title={meta.source}>{meta.note ?? '没拿到'}</div>
}

function Tile({ title, meta, children }: { title: string; meta?: LiveMeta; children: ReactNode }) {
  return (
    <div className="rounded border border-bg-border bg-bg-elevated/40 px-3 py-2 space-y-1 min-w-0">
      <div className="text-[11px] text-text-muted" title={meta?.source}>{title}</div>
      {children}
      <MetaNote meta={meta} />
    </div>
  )
}

// ── 顶部：时段、市场时间、刷新 ───────────────────────────────────────────────

function StatusBar({ d, auto, setAuto, fetching, refetch, updatedAt }: {
  d: LiveData; auto: boolean; setAuto: (v: boolean) => void; fetching: boolean; refetch: () => void; updatedAt: number
}) {
  const s = d.session
  const lagMin = s.market_time && ['trading', 'closing_auction'].includes(s.phase)
    ? (new Date(s.server_time).getTime() - new Date(s.market_time).getTime()) / 60000 : 0
  return (
    <div className="card p-3 space-y-2 border border-warn/30">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={cn('inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded', PHASE_TONE[s.phase])}>
          <Radio className={cn('w-3 h-3', s.phase === 'trading' && 'animate-pulse')} />{s.phase_zh}
        </span>
        <span className="text-xs text-text-secondary">
          行情时间 <span className={NUM}>{s.market_time ? s.market_time.replace('T', ' ').slice(5, 19) : '—'}</span>
          <span className="text-text-muted">（市场自己的时间戳）</span>
        </span>
        <span className="text-[11px] text-text-muted">
          执行 <span className="text-text-secondary font-mono">{s.ref_date ?? '—'}</span> 锁定的预案
          {s.target_date && <> → <span className="font-mono">{s.target_date}</span>{s.target_is_provisional && '（按工作日推测）'}</>}
        </span>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-text-muted">
          <span>页面刷新于 <span className={NUM}>{new Date(updatedAt).toLocaleTimeString('zh-CN', { hour12: false })}</span></span>
          <label className="inline-flex items-center gap-1 cursor-pointer" title="只在交易时段、页面在前台时刷新">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-current" />
            自动刷新{s.poll_seconds ? `（${s.poll_seconds >= 60 ? `${Math.round(s.poll_seconds / 60)} 分钟` : `${s.poll_seconds} 秒`}）` : '（现在不用）'}
          </label>
          <button onClick={refetch} disabled={fetching}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-bg-border hover:text-text-primary disabled:opacity-60">
            <RefreshCw className={cn('w-3 h-3', fetching && 'animate-spin')} />刷新
          </button>
        </span>
      </div>
      <div className="flex items-start gap-1.5 text-[11px] text-warn">
        <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
        <span>{s.note}{s.live && s.phase !== 'closed' && <>。这里只读：不保存、不改锁定的预案、不进活跃事件表。</>}</span>
      </div>
      {s.calendar_behind && (
        <div className="text-[11px] text-down">交易日历只到 {s.calendar_last}，上一交易日可能算错，「执行哪天的预案」不一定对</div>
      )}
      {lagMin > 2 && <div className="text-[11px] text-warn">行情时间比服务器时间落后 {lagMin.toFixed(0)} 分钟，可能停牌较多或行情源卡住</div>}
      {(d.quotes.missing.length > 0 || d.quotes.error) && (
        <div className="text-[11px] text-warn" title={d.quotes.missing.join(' ')}>
          行情 {d.quotes.ok}/{d.quotes.requested} 只{d.quotes.missing.length ? `，${d.quotes.missing.length} 只没拿到（显示「没行情」，不算 0）` : ''}{d.quotes.error ? `；${d.quotes.error}` : ''}
        </div>
      )}
    </div>
  )
}

// ── ① 市场此刻 ──────────────────────────────────────────────────────────────

function MarketNow({ d }: { d: LiveData }) {
  const m = d.market
  const b = m.breadth, l = m.limit, a = m.amount
  const COHORT = [['limit_up', '昨日涨停'], ['multi_board', '昨日连板'], ['broken_board', '昨日炸板']] as const
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {m.indices.map((x) => (
          <Tile key={x.code} title={x.name}>
            <div className="flex items-baseline gap-2">
              <span className={cn(NUM, 'text-sm text-text-primary')}>{x.price?.toFixed(2) ?? '—'}</span>
              {x.status === 'ok' ? <span className={cn(NUM, 'text-xs', pctCls(x.pct))}>{fmtPct(x.pct)}</span>
                : <span className="text-[11px] text-warn">{x.status === 'stale' ? '非今日' : '没拿到'}</span>}
            </div>
          </Tile>
        ))}
      </div>
      {!d.session.live ? (
        <div className="text-[11px] text-text-muted">没开盘，不取全市场的实时数（涨跌家数、涨停池、成交额）</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <Tile title="涨跌家数（东财涨跌分布）" meta={b?.meta}>
            {b?.up != null ? (
              <div className="text-xs space-x-2">
                <span className="text-up">涨 <span className={NUM}>{b.up}</span></span>
                <span className="text-down">跌 <span className={NUM}>{b.down}</span></span>
                <span className="text-text-muted">平 <span className={NUM}>{b.flat}</span></span>
                <span className="text-text-muted">涨停 <span className={NUM}>{b.limit_up}</span> · 跌停 <span className={NUM}>{b.limit_down}</span></span>
              </div>
            ) : <div className="text-xs text-text-muted">—</div>}
          </Tile>
          <Tile title="封板（东财实时涨停池 + 炸板池）" meta={l?.meta}>
            {l?.sealed != null ? (
              <div className="text-xs space-x-2">
                <span className="text-up">封 <span className={NUM}>{l.sealed}</span></span>
                <span className="text-warn">炸 <span className={NUM}>{l.broken}</span></span>
                <span className="text-text-secondary">封板率 <span className={NUM}>{l.seal_rate != null ? `${(l.seal_rate * 100).toFixed(0)}%` : '—'}</span></span>
                <span className="text-text-secondary">最高 <span className={NUM}>{l.max_board ?? '—'}</span> 板</span>
              </div>
            ) : <div className="text-xs text-text-muted">—</div>}
          </Tile>
          <Tile title="两市成交额" meta={a?.meta}>
            {a?.amount != null ? (
              <div className="text-xs space-x-2">
                <span className="text-text-secondary">此刻 <span className={NUM}>{yi(a.amount)}</span></span>
                {a.is_trading && <span className="text-text-secondary" title={a.meta.note ?? ''}>预估全天 <span className={NUM}>{yi(a.estimate)}</span></span>}
                <span className="text-text-muted">昨日 <span className={NUM}>{yi(a.prev_amount)}</span>
                  {a.estimate_vs_prev_pct != null && <span className={cn('ml-1', NUM, pctCls(a.estimate_vs_prev_pct))}>{fmtPct(a.estimate_vs_prev_pct)}</span>}</span>
              </div>
            ) : <div className="text-xs text-text-muted">—</div>}
          </Tile>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {COHORT.map(([k, label]) => {
          const c = m.cohorts[k]
          return (
            <Tile key={k} title={`${label}今日（${d.session.ref_date ?? '—'} 的那批票，此刻）`}>
              {!c || !c.members ? <div className="text-xs text-text-muted">{c ? '这一组没有成员' : '没有上一交易日'}</div> : (
                <div className="text-xs space-x-2">
                  <span className="text-text-secondary">中位 <span className={cn(NUM, pctCls(c.median_pct))}>{fmtPct(c.median_pct)}</span></span>
                  <span className="text-text-secondary">红盘 <span className={NUM}>{c.red_ratio != null ? `${(c.red_ratio * 100).toFixed(0)}%` : '—'}</span></span>
                  <span className="text-up">封 <span className={NUM}>{c.sealed}</span></span>
                  <span className="text-warn">炸 <span className={NUM}>{c.broken}</span></span>
                  {c.limit_down > 0 && <span className="text-down">跌停 <span className={NUM}>{c.limit_down}</span></span>}
                  <span className={cn('text-[10px]', c.quoted < c.members ? 'text-warn' : 'text-text-muted')}>
                    样本 {c.quoted}/{c.members}
                  </span>
                </div>
              )}
            </Tile>
          )
        })}
      </div>
    </div>
  )
}

// ── ② 今日预案 ──────────────────────────────────────────────────────────────

function PlanNow({ d, onOpenPrepare }: { d: LiveData; onOpenPrepare: (date: string) => void }) {
  const p = d.plan
  if (!p.source) {
    return <div className="text-xs text-text-muted">{p.ref_date ? `${p.ref_date} 没有预案（没锁定，也没草稿）。` : '没有上一交易日。'}这里只跟踪，预案要在盘后研究里写、锁定。</div>
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {p.source === 'locked'
          ? <span className={cn('px-1.5 py-0.5 rounded', p.pre_open ? 'bg-up/10 text-up' : 'bg-warn/15 text-warn')}>
              {p.ref_date} 锁定 v{p.version}{p.pre_open ? '（盘前）' : '（事后记录，不是盘前判断）'}
            </span>
          : <span className="px-1.5 py-0.5 rounded bg-warn/15 text-warn">{p.ref_date} 的草稿（还没锁定）</span>}
        {p.posture && <span className="text-text-secondary">姿态：{POSTURE_LABEL[p.posture]}</span>}
        {p.flat_plan && <span className="text-text-secondary">空仓预案</span>}
        {p.ref_date && <button onClick={() => onOpenPrepare(p.ref_date!)} className="text-accent hover:underline">看完整准备 →</button>}
      </div>
      {(p.key_points?.length || p.invalidation?.length) ? (
        <div className="grid sm:grid-cols-2 gap-2 text-[11px]">
          {!!p.key_points?.length && (
            <div className="rounded border border-bg-border px-2 py-1.5">
              <div className="text-text-muted mb-0.5">三个观察点（对照盘面自己判断）</div>
              <ol className="list-decimal list-inside text-text-secondary space-y-0.5">{p.key_points.map((k, i) => <li key={i}>{k}</li>)}</ol>
            </div>
          )}
          {!!p.invalidation?.length && (
            <div className="rounded border border-down/30 px-2 py-1.5">
              <div className="text-text-muted mb-0.5">判断失效条件（出现了就停下来重新看）</div>
              <ul className="list-disc list-inside text-text-secondary space-y-0.5">{p.invalidation.map((k, i) => <li key={i}>{k}</li>)}</ul>
            </div>
          )}
        </div>
      ) : null}
      {p.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-text-muted text-left border-b border-bg-border">
              <tr><th className={TH}>候选</th><th className={TH}>情景</th><th className={cn(TH, 'text-right')}>现价</th>
                <th className={cn(TH, 'text-right')}>涨跌</th><th className={TH}>涨停</th>
                <th className={cn(TH, 'text-right')} title="现价相对计划止损价；负数 = 此刻已在止损价下方">计划止损 / 距离</th>
                <th className={TH}>入场触发（它自己的）</th><th className={TH}>失效条件</th><th className={TH}>观察触发 / 关联</th><th className={TH} /></tr>
            </thead>
            <tbody>
              {p.rows.map((r) => (
                <tr key={r.stock_code} className={cn('border-b border-bg-border/50 align-top', r.below_stop && 'bg-down/5')}>
                  <td className={TD}><StockLink code={r.stock_code} name={r.stock_name ?? r.live.name} /></td>
                  <td className={TD}>{r.scenario ?? '—'}</td>
                  <td className={cn(TD, 'text-right', NUM)}>{r.live.price?.toFixed(2) ?? '—'}</td>
                  <td className={cn(TD, 'text-right')}><Pct f={r.live} /></td>
                  <td className={TD}><LimitState f={r.live} /></td>
                  <td className={cn(TD, 'text-right', NUM)}>
                    {r.planned_stop != null ? r.planned_stop.toFixed(2) : '—'}
                    {r.to_stop_pct != null && <span className={cn('ml-1', r.below_stop ? 'text-down font-semibold' : 'text-text-muted')}>
                      {r.below_stop ? '已破 ' : ''}{fmtPct(r.to_stop_pct)}</span>}
                  </td>
                  <td className="px-2 py-1 min-w-[10rem] max-w-[18rem] text-text-secondary whitespace-pre-wrap">{r.entry_trigger}</td>
                  <td className="px-2 py-1 min-w-[8rem] max-w-[16rem] text-text-secondary whitespace-pre-wrap">{r.invalidation}</td>
                  <td className="px-2 py-1 min-w-[8rem] max-w-[16rem] text-text-muted whitespace-pre-wrap">
                    {[r.watch_trigger, r.relation_note].filter(Boolean).join('\n') || '—'}
                  </td>
                  <td className={TD}>
                    <Link to={`/pre-trade-check?code=${r.stock_code}${r.planned_stop != null ? `&planned_stop=${r.planned_stop}` : ''}${r.position_limit != null ? `&position_pct=${r.position_limit}` : ''}`}
                          className="text-accent hover:underline text-[11px]" title="预案本身不是买入许可：去买入检查对一遍纪律">买入检查 →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-1 text-[10px] text-text-muted">入场有没有触发要你对照分时判断，系统不替你判；「已破」只说明此刻价格在计划止损下方。</div>
        </div>
      )}
    </div>
  )
}

// ── ③ 重点高标与关联股 ───────────────────────────────────────────────────────

function LeadersNow({ d }: { d: LiveData }) {
  if (!d.leaders.length) return <div className="text-xs text-text-muted">观察池里还没有 ★ 重点高标。在盘后研究的观察池里标 ★，它的关联股会出现在这里。</div>
  return (
    <div className="space-y-3">
      {d.leaders.map((L) => {
        const rows = [...L.relations].sort((a, b) => (b.live.pct ?? -999) - (a.live.pct ?? -999))
        return (
          <div key={L.code} className="rounded border border-bg-border">
            <div className="px-3 py-1.5 border-b border-bg-border flex flex-wrap items-center gap-2 text-xs">
              <span className="text-purple-300">★</span>
              <StockLink code={L.code} name={L.name} />
              <Pct f={L.live} /><LimitState f={L.live} />
              <EventChips events={L.live.events} labels={d.labels.events} />
              <span className="ml-auto text-[11px] text-text-muted">
                {Object.entries(L.by_response).map(([k, n]) => (
                  <span key={k} className={cn('ml-2', SYS_TONE[k as SystemResponse])}>{d.labels.responses[k as SystemResponse]} {n}</span>
                ))}
              </span>
            </div>
            {!rows.length ? <div className="px-3 py-2 text-[11px] text-text-muted">还没有录入关联股</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-text-muted text-left"><tr>
                    <th className={TH}>关联股</th><th className={TH}>属性</th><th className={cn(TH, 'text-right')}>涨跌</th>
                    <th className={TH}>涨停 / 首封</th><th className={TH} title="按写死的条件分：涨停 / ≥5% / 高标转弱时 ≥5% / 没满足 / 数据不足。尚未满足 ≠ 没响应">系统响应（临时）</th></tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.code} className="border-t border-bg-border/50">
                        <td className={TD}><StockLink code={r.code} name={r.name} /></td>
                        <td className="px-2 py-1 text-[11px] text-text-secondary">
                          {r.attributes.map((a, i) => <span key={i} className="mr-1.5" title={ATTR_LABEL[a.type]}><span className="text-text-muted">{a.basis}</span> {a.value}</span>)}
                        </td>
                        <td className={cn(TD, 'text-right')}><Pct f={r.live} /></td>
                        <td className={TD}><LimitState f={r.live} /></td>
                        <td className={cn(TD, 'text-[11px]', r.system_response && SYS_TONE[r.system_response])}>
                          {r.system_response ? d.labels.responses[r.system_response] : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}
      <div className="text-[10px] text-text-muted">首封时间两边都有，才谈得上领先 / 同步 / 滞后——要记的话去盘后研究的关联区记，这里不写。</div>
    </div>
  )
}

// ── ④ 观察池 / ⑤ 人气核心池 / ⑥ 涨停池 ─────────────────────────────────────

function WatchNow({ d }: { d: LiveData }) {
  if (!d.watch.length) return <div className="text-xs text-text-muted">观察池是空的</div>
  const rows = [...d.watch].sort((a, b) => (b.live?.pct ?? -999) - (a.live?.pct ?? -999))
  return (
    <div className="flex flex-wrap gap-1.5">
      {rows.map((w) => (
        <span key={w.code} className="inline-flex items-center gap-1.5 rounded border border-bg-border px-2 py-1 text-xs" title={w.note ?? ''}>
          {w.deep && <span className="text-purple-300">★</span>}
          <StockLink code={w.code} name={w.name} />
          <Pct f={w.live} /><LimitState f={w.live} />
          <EventChips events={w.live?.events ?? []} labels={d.labels.events} />
        </span>
      ))}
    </div>
  )
}

const CORE_FILTERS = [['events', '盘中有事件'], ['sealed', '此刻封板'], ['broken', '炸板'], ['all', '全部']] as const

function CoresNow({ d }: { d: LiveData }) {
  const [f, setF] = useState<typeof CORE_FILTERS[number][0]>('events')
  const [all, setAll] = useState(false)
  const c = d.cores
  const rows = useMemo(() => {
    const keep = c.rows.filter((r) => f === 'all' || (f === 'events' ? r.live?.events.length : r.live?.limit === f))
    return keep.sort((a, b) => (b.live?.pct ?? -999) - (a.live?.pct ?? -999))
  }, [c.rows, f])
  if (c.note) return <div className="text-xs text-warn">{c.note}</div>
  const shown = all ? rows : rows.slice(0, 30)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        <span className="text-text-muted mr-1">{c.ref_date} 的人气核心池（{c.frozen ? '收盘冻结' : '现算'}，{c.rows.length} 只）× 此刻行情：</span>
        {CORE_FILTERS.map(([k, label]) => (
          <button key={k} onClick={() => setF(k)}
                  className={cn('px-2 py-0.5 rounded border', f === k ? 'border-accent/50 text-accent' : 'border-bg-border text-text-muted hover:text-text-secondary')}>
            {label}
          </button>
        ))}
      </div>
      {!rows.length ? <div className="text-xs text-text-muted">{d.session.live ? '没有符合的' : '没开盘，没有盘中事件'}</div> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-text-muted text-left border-b border-bg-border"><tr>
              <th className={TH}>股票</th><th className={TH}>入选依据</th>
              <th className={cn(TH, 'text-right')} title="上一交易日收盘涨跌">昨日</th>
              <th className={cn(TH, 'text-right')}>现涨跌</th><th className={TH}>涨停 / 首封</th><th className={TH}>盘中事件（临时）</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.code} className="border-b border-bg-border/50">
                  <td className={TD}>{r.watched && <span className="text-purple-300 mr-0.5" title="在我的观察池">●</span>}<StockLink code={r.code} name={r.name} />
                    <span className="ml-1 text-[10px] text-text-muted">{r.board}</span></td>
                  <td className="px-2 py-1 text-[10px] text-text-secondary">{r.sources.map((s) => c.source_labels[s] ?? (s === 'manual' ? '我的重点' : s)).join(' · ')}</td>
                  <td className={cn(TD, 'text-right', NUM, pctCls(r.ref_pct))}>{fmtPct(r.ref_pct)}{r.ref_board ? <span className="ml-1 text-text-muted">{r.ref_board}板</span> : null}</td>
                  <td className={cn(TD, 'text-right')}><Pct f={r.live} /></td>
                  <td className={TD}><LimitState f={r.live} /></td>
                  <td className={TD}><EventChips events={r.live?.events ?? []} labels={d.labels.events} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 30 && <button onClick={() => setAll(!all)} className="mt-1 text-[11px] text-accent hover:underline">{all ? '收起' : `展开全部 ${rows.length} 只`}</button>}
        </div>
      )}
    </div>
  )
}

function PoolNow({ d }: { d: LiveData }) {
  const [open, setOpen] = useState(false)
  if (!d.session.live) return <div className="text-xs text-text-muted">没开盘</div>
  if (!d.limit_pool.length) return <div className="text-xs text-text-muted">{d.market.limit?.meta.status === 'ok' ? '此刻没有封板的票' : '涨停池这次没拿到'}</div>
  const rows = [...d.limit_pool].sort((a, b) => (b.board ?? 1) - (a.board ?? 1) || (a.first_time ?? '').localeCompare(b.first_time ?? ''))
  const shown = open ? rows : rows.slice(0, 15)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-text-muted text-left border-b border-bg-border"><tr>
          <th className={TH}>股票</th><th className={cn(TH, 'text-right')}>连板</th><th className={TH}>首封</th><th className={TH}>最后封</th>
          <th className={cn(TH, 'text-right')}>开板</th><th className={TH}>东财行业</th></tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.code} className="border-b border-bg-border/50">
              <td className={TD}><StockLink code={r.code} name={r.name} />
                {r.in_core && <span className="ml-1 text-[10px] px-1 rounded bg-accent/15 text-accent">核心池</span>}
                {r.watched && <span className="ml-1 text-[10px] px-1 rounded bg-purple-400/15 text-purple-300">观察</span>}</td>
              <td className={cn(TD, 'text-right', NUM)}>{r.board ?? '—'}</td>
              <td className={cn(TD, NUM)}>{r.first_time?.slice(0, 5) ?? '—'}</td>
              <td className={cn(TD, NUM)}>{r.last_time?.slice(0, 5) ?? '—'}</td>
              <td className={cn(TD, 'text-right', NUM)}>{r.broken_times ?? '—'}</td>
              <td className={cn(TD, 'text-text-secondary')}>{r.industry ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 15 && <button onClick={() => setOpen(!open)} className="mt-1 text-[11px] text-accent hover:underline">{open ? '收起' : `展开全部 ${rows.length} 只`}</button>}
    </div>
  )
}

// ── 整页 ────────────────────────────────────────────────────────────────────

export function LivePanel({ onOpenPrepare }: { onOpenPrepare: (date: string) => void }) {
  const [auto, setAuto] = useState(true)
  const q = useQuery({
    queryKey: ['recap-live'], queryFn: fetchLive,
    // 交易时段 30 秒、午休到下午开盘、开盘前等到 09:15；收盘 / 休市不自动刷。页面在后台时不刷
    refetchInterval: (query) => {
      const s = query.state.data?.session.poll_seconds
      return auto && s ? s * 1000 : false
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
  })
  if (q.isLoading) return <LoadingSpinner />
  if (q.isError || !q.data) {
    return <div className="card p-6 text-sm text-down">盘中数据读取失败：{(q.error as Error | null)?.message ?? '未知错误'}</div>
  }
  const d = q.data
  return (
    <div className="space-y-3">
      <StatusBar d={d} auto={auto} setAuto={setAuto} fetching={q.isFetching} refetch={() => q.refetch()} updatedAt={q.dataUpdatedAt} />
      <Section id="live-market" step="①" title="市场此刻" right={<Provisional />}>
        <MarketNow d={d} />
      </Section>
      <Section id="live-plan" step="②" title="今天执行的预案" right={<span className="text-[11px] text-text-muted">预案是盘前写的；这里只把此刻的价格摆在旁边</span>}>
        <PlanNow d={d} onOpenPrepare={onOpenPrepare} />
      </Section>
      <Section id="live-leaders" step="③" title="重点高标与关联股" right={<Provisional />}>
        <LeadersNow d={d} />
      </Section>
      <Section id="live-watch" step="④" title="我的观察池">
        <WatchNow d={d} />
      </Section>
      <Section id="live-cores" step="⑤" title="人气核心池 · 此刻" right={<Provisional />}>
        <CoresNow d={d} />
      </Section>
      <Section id="live-pool" step="⑥" title="此刻涨停池" right={<span className="text-[11px] text-text-muted">按连板高度、首封时间排；东财实时涨停池</span>}>
        <PoolNow d={d} />
      </Section>
    </div>
  )
}
