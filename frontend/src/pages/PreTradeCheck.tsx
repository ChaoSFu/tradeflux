/**
 * 买入检查 Pre-Trade Check
 *
 * 在按下买入键之前，让主观冲动与客观证据完成一次强制对账。两个场景：
 *   A. 实时：准备买某只票，现在就对照交易规则检查
 *   B. 历史：复盘某一笔——严格站在当时（as_of）看，只用那一刻能知道的信息
 *
 * **这一页不找买点。** 票和时刻都是你给的；系统只把事实摆出来、按你的纪律对账，
 * 结论是 READY / WAIT / BLOCKED，没有分数。READY 只代表没发现冲突，买不买由你决定。
 * 规则全在后端（pre_trade_rules.py），这里只负责输入、展示证据、收人工回答。
 */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ClipboardCheck, RefreshCw } from 'lucide-react'
import { cn } from '@/utils/cn'
import { useAuthStore } from '@/store/auth'
import { LoginModal } from '@/components/auth/LoginModal'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import {
  EMPTY_ANSWERS, evaluatePreTrade, fetchPreTradeCheck, fetchPreTradeContext, fetchPreTradeHistory,
  fetchPreTradeOutcome, type EvaluatePayload, type EvaluateResult, type ManualAnswers,
} from '@/api/preTradeCheck'
import { StockPicker } from '@/components/preTrade/StockPicker'
import { VerdictPanel } from '@/components/preTrade/VerdictPanel'
import { CheckModules } from '@/components/preTrade/CheckModules'
import { ManualQuestions } from '@/components/preTrade/ManualQuestions'
import { OutcomePanel } from '@/components/preTrade/OutcomePanel'
import { QualityBadge } from '@/components/preTrade/levels'

const inputCls = 'w-full bg-bg-elevated border border-bg-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50'
const labelCls = 'text-[11px] text-text-muted mb-1 block'
const VERDICT_TONE = { READY: 'text-accent', WAIT: 'text-warn', BLOCKED: 'text-danger' } as const

const pad = (n: number) => String(n).padStart(2, '0')
function nowLocalSec() {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
/** datetime-local 可能不带秒；后端要的是完整时刻 */
const withSeconds = (s: string) => (s.length === 16 ? `${s}:00` : s)
/** 落在开盘前 / 午休 / 收盘后的时刻，挪进最近的交易时段——一键改日期时用 */
function clampSession(hms: string) {
  if (hms < '09:30:00') return '09:45:00'
  if (hms > '11:30:00' && hms < '13:00:00') return '13:00:00'
  if (hms > '15:00:00') return '14:55:00'
  return hms
}
const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六']
const weekdayOf = (ymd: string) => { const [y, m, d] = ymd.split('-').map(Number); return new Date(y, m - 1, d).getDay() }
/**
 * 切到「历史时刻」的默认值。之前直接填今天：周末打开就落在非交易日，满屏「未知」（2026-09-12 生产）。
 * 有后端日历就用前一交易日；还没拿到日历时至少避开周末。
 */
function defaultHistorical(ctx?: { is_trading_day: boolean | null; prev_trade_date: string | null }) {
  const now = nowLocalSec()
  if (ctx?.is_trading_day === false && ctx.prev_trade_date) return `${ctx.prev_trade_date}T${clampSession(now.slice(11))}`
  const d = new Date()
  if (d.getDay() !== 0 && d.getDay() !== 6) return now
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${clampSession(now.slice(11))}`
}
const num = (s: string) => (s.trim() === '' || Number.isNaN(Number(s)) ? null : Number(s))
const pctTxt = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

export default function PreTradeCheck() {
  const [params] = useSearchParams()
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const [showLogin, setShowLogin] = useState(false)

  // ── 输入（URL 可带入：?code=&as_of=&price=&position_pct=&planned_stop=）──────
  const [code, setCode] = useState(params.get('code') ?? '')
  const [name, setName] = useState<string | null>(null)
  const [mode, setMode] = useState<'LIVE' | 'HISTORICAL'>(params.get('as_of') ? 'HISTORICAL' : 'LIVE')
  const [asOf, setAsOf] = useState(params.get('as_of') ?? '')
  const [price, setPrice] = useState(params.get('price') ?? '')
  const [position, setPosition] = useState(params.get('position_pct') ?? '')
  const [stop, setStop] = useState(params.get('planned_stop') ?? '')
  const [reason, setReason] = useState('')
  const [sectorId, setSectorId] = useState<number | null>(null)
  const [budget, setBudget] = useState('')
  const [stress, setStress] = useState('')
  const [answers, setAnswers] = useState<ManualAnswers>(EMPTY_ANSWERS)
  const [result, setResult] = useState<EvaluateResult | null>(null)
  const [resultPayload, setResultPayload] = useState('')

  const codeOk = /^\d{6}$/.test(code)
  const asOfOk = mode === 'LIVE' || /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(asOf)
  const asOfParam = mode === 'LIVE' ? null : withSeconds(asOf)

  // 换票 / 换时刻：答案和结论都作废——上一只票的「否」不能挪给这一只
  useEffect(() => { setAnswers(EMPTY_ANSWERS); setResult(null); setSectorId(null) }, [code, mode, asOf])

  const ctxQ = useQuery({
    queryKey: ['pretrade-context', code, asOfParam ?? 'live', sectorId],
    queryFn: () => fetchPreTradeContext({ stock_code: code, as_of: asOfParam, sector_id: sectorId }),
    enabled: codeOk && asOfOk,
    staleTime: mode === 'LIVE' ? 60_000 : Infinity,
    retry: false,
  })
  const ctx = ctxQ.data
  useEffect(() => { if (ctx?.stock.name && !name) setName(ctx.stock.name) }, [ctx, name])

  const payload: EvaluatePayload = useMemo(() => ({
    stock_code: code, as_of: asOfParam,
    intended_price: num(price), position_pct: num(position), planned_stop: num(stop),
    reason, thesis_sector_id: sectorId ?? ctx?.sector.thesis?.id ?? null, manual_answers: answers,
    ...(num(budget) != null ? { account_risk_budget_pct: num(budget)! } : {}),
    ...(num(stress) != null ? { stress_loss_pct: num(stress)! } : {}),
  }), [code, asOfParam, price, position, stop, reason, sectorId, ctx, answers, budget, stress])

  const evalM = useMutation({
    mutationFn: evaluatePreTrade,
    onSuccess: (r, p) => { setResult(r); setResultPayload(JSON.stringify(p)) },
  })
  const stale = result != null && resultPayload !== '' && resultPayload !== JSON.stringify(payload)

  const histQ = useQuery({ queryKey: ['pretrade-history'], queryFn: () => fetchPreTradeHistory(15),
                           enabled: isLoggedIn, staleTime: 30_000 })
  useEffect(() => { if (evalM.isSuccess) histQ.refetch() }, [evalM.isSuccess]) // eslint-disable-line react-hooks/exhaustive-deps

  const outcomeQ = useQuery({
    queryKey: ['pretrade-outcome', result?.id],
    queryFn: () => fetchPreTradeOutcome(result!.id!),
    enabled: false,
  })

  const openSaved = async (id: number) => {
    const r = await fetchPreTradeCheck(id)
    setResult(r); setResultPayload('')
  }

  const beforeEntry = ctx ? ctx.as_of.slice(11, 16) < ctx.defaults.earliest_normal_entry : false
  const answeredAll = ctx ? ctx.manual_questions.every((q) => answers[q.key as keyof ManualAnswers] !== null) : false

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
        <ClipboardCheck className="w-3.5 h-3.5 text-accent" />
        在按下买入键之前，让冲动和证据对一次账。票和时刻由你给，系统只摆事实、按你的纪律对账——
        <span className="text-text-secondary">不找买点，不打分，READY 也不是买入信号。</span>
      </div>

      {!isLoggedIn && (
        <div className="card flex flex-wrap items-center justify-between gap-2 p-3 text-xs text-warn">
          需要登录：检查要对照你的交易记录，结果也会存档供复盘。
          <button onClick={() => setShowLogin(true)} className="rounded border border-accent/40 px-2.5 py-1 text-accent hover:bg-accent/10">登录</button>
          {showLogin && <LoginModal onClose={() => setShowLogin(false)} />}
        </div>
      )}

      {/* ── 输入 ───────────────────────────────────────────────────────── */}
      <div className="card p-4 space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="min-w-0">
            <label className={labelCls}>股票 *</label>
            <StockPicker code={code} name={name} inputCls={inputCls}
                         onPick={(c, n) => { setCode(c); setName(n) }} />
          </div>
          <div className="min-w-0">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="text-[11px] text-text-muted">评判时间（as_of）*</span>
              <div className="flex gap-1">
                {(['LIVE', 'HISTORICAL'] as const).map((m) => (
                  <button key={m} type="button"
                          onClick={() => { setMode(m); if (m === 'HISTORICAL' && !asOf) setAsOf(defaultHistorical(ctx)) }}
                          className={cn('rounded border px-1.5 text-[11px] leading-5',
                            mode === m ? 'border-accent/50 bg-accent/15 text-accent' : 'border-bg-border text-text-muted hover:text-text-secondary')}>
                    {m === 'LIVE' ? '此刻' : '历史时刻'}
                  </button>
                ))}
              </div>
            </div>
            {mode === 'HISTORICAL'
              ? <input type="datetime-local" step={1} value={asOf} onChange={(e) => setAsOf(e.target.value)} className={inputCls} />
              : <div className={cn(inputCls, 'text-text-muted')}>按点「开始检查」那一刻取数</div>}
          </div>
          <div className="min-w-0">
            <label className={labelCls}>本次交易逻辑板块</label>
            <select value={sectorId ?? ctx?.sector.thesis?.id ?? ''} disabled={!ctx?.sector.options.length}
                    onChange={(e) => setSectorId(e.target.value ? Number(e.target.value) : null)}
                    className={inputCls}>
              {!ctx?.sector.options.length && <option value="">{ctx ? '库里没有板块归属' : '先选股票'}</option>}
              {ctx?.sector.options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}{o.is_primary ? '（主板块）' : o.is_watched ? '（关注）' : ''}
                  {o.prev_limit_up ? ` · 昨日涨停${o.prev_limit_up}` : ''}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-5">
          <div><label className={labelCls}>计划/实际买入价</label><input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="不填按 as_of 价" className={inputCls} /></div>
          <div><label className={labelCls}>计划仓位 %</label><input value={position} onChange={(e) => setPosition(e.target.value)} className={inputCls} /></div>
          <div><label className={labelCls}>计划失效价</label><input value={stop} onChange={(e) => setStop(e.target.value)} className={inputCls} /></div>
          <div><label className={labelCls}>单笔风险预算 %</label><input value={budget} onChange={(e) => setBudget(e.target.value)} placeholder={String(ctx?.defaults.account_risk_budget_pct ?? 1.5)} className={inputCls} /></div>
          <div><label className={labelCls}>压力损失 %</label><input value={stress} onChange={(e) => setStress(e.target.value)} placeholder={String(ctx?.defaults.stress_loss_pct ?? 8)} className={inputCls} /></div>
        </div>
        <div>
          <label className={labelCls}>买入理由</label>
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
                    placeholder="为什么是它、为什么是现在？" className={cn(inputCls, 'resize-none')} />
        </div>
      </div>

      {/* ── 事实概览：as_of 那一刻取到了什么、从哪来、准不准 ─────────────── */}
      {codeOk && asOfOk && (
        <div className="card p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold text-text-primary">
              事实概览{ctx && <span className="ml-2 font-normal text-text-muted">
                {ctx.mode === 'LIVE' ? '实时' : '历史'} · as_of {ctx.as_of.replace('T', ' ')} · 前一交易日 {ctx.prev_trade_date ?? '—'}
                {ctx.calendar?.behind && <span className="text-warn">（交易日历只到 {ctx.calendar.last}，可能不准）</span>}
              </span>}
            </span>
            {mode === 'LIVE' && (
              <button onClick={() => ctxQ.refetch()} disabled={ctxQ.isFetching}
                      className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent disabled:opacity-50">
                <RefreshCw className={cn('w-3 h-3', ctxQ.isFetching && 'animate-spin')} />重新取数
              </button>
            )}
          </div>
          {ctx?.is_trading_day === false && (
            <div className="flex flex-wrap items-center gap-2 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
              <span>
                {ctx.trade_date}（周{WEEKDAY[weekdayOf(ctx.trade_date)]}）不是交易日：这个时刻下不了单，也没有行情，下面的「未知」都是因为这个。
              </span>
              {ctx.prev_trade_date && (
                <button type="button" className="rounded border border-warn/50 px-2 py-0.5 hover:bg-warn/20"
                        onClick={() => { setMode('HISTORICAL'); setAsOf(`${ctx.prev_trade_date}T${clampSession(ctx.as_of.slice(11, 19))}`) }}>
                  改成前一交易日 {ctx.prev_trade_date} {clampSession(ctx.as_of.slice(11, 19))}
                </button>
              )}
            </div>
          )}
          {ctxQ.isPending && <div className="text-xs text-text-muted">正在取 as_of 那一刻的行情、板块、个股和交易记录……</div>}
          {ctxQ.isError && <div className="text-xs text-danger">取数失败：{(ctxQ.error as Error).message}</div>}
          {ctx && (
            <>
              <div className="grid gap-3 text-xs md:grid-cols-4">
                <div>
                  <div className="text-text-muted">{ctx.stock.name ?? code} · as_of 价</div>
                  <div className="font-mono text-base text-text-primary">
                    {ctx.intraday.price ?? '—'} <span className="text-xs">{pctTxt(ctx.intraday.pct)}</span>
                  </div>
                  <div className="text-text-muted">均价 {ctx.intraday.vwap?.toFixed(2) ?? '—'} · 结构 {ctx.intraday.structure?.status ?? '—'}</div>
                </div>
                <div>
                  <div className="text-text-muted">核心指数</div>
                  {ctx.market.indexes.map((i) => (
                    <div key={i.code} className="font-mono">{i.name} {pctTxt(i.pct)}</div>
                  ))}
                </div>
                <div>
                  <div className="text-text-muted">板块 / 生命周期</div>
                  <div>{ctx.sector.thesis?.name ?? '无板块归属'}</div>
                  <div>{ctx.leader.lifecycle
                    ? `${ctx.leader.lifecycle.date} 收盘：${LIFECYCLE_ZH[ctx.leader.lifecycle.state] ?? ctx.leader.lifecycle.state}`
                    : '无高标周期'}</div>
                </div>
                <div>
                  <div className="text-text-muted">交易记录（as_of 之前）</div>
                  {ctx.discipline.available ? (
                    <>
                      <div>今天已买 {ctx.discipline.today_buys?.length ?? 0} 笔</div>
                      <div>连续亏损 {ctx.discipline.consecutive_losses ?? 0} 笔</div>
                    </>
                  ) : <div className="text-text-muted">{ctx.discipline.reason}</div>}
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <tbody>
                    {ctx.data_quality.map((r) => (
                      <tr key={r.module} className="border-t border-bg-border/40">
                        <td className="py-1 pr-2 whitespace-nowrap text-text-secondary">{r.module}</td>
                        <td className="py-1 pr-2 whitespace-nowrap"><QualityBadge q={r.quality} /></td>
                        <td className="py-1 pr-2 whitespace-nowrap text-text-muted">{r.source}</td>
                        <td className="py-1 pr-2 whitespace-nowrap font-mono text-text-muted">{r.observed_at?.replace('T', ' ') ?? '—'}</td>
                        <td className="py-1 text-text-muted">{r.notes.join('；')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {ctx && (
        <ManualQuestions questions={ctx.manual_questions} answers={answers} onChange={setAnswers}
                         asOfBeforeEntry={beforeEntry} earliest={ctx.defaults.earliest_normal_entry}
                         recent={ctx.discipline.recent_same_stock ?? []}
                         todayBuys={ctx.discipline.today_buys ?? []}
                         maxTrades={ctx.defaults.max_trades_per_day} />
      )}

      {ctx && (
        <div className="flex flex-wrap items-center gap-3">
          <button onClick={() => evalM.mutate(payload)} disabled={evalM.isPending || !codeOk || !asOfOk}
                  className="rounded-lg bg-accent px-5 py-2 text-sm font-medium text-bg-base hover:bg-accent/90 disabled:opacity-50">
            {evalM.isPending ? '检查中……' : '开始检查'}
          </button>
          {!answeredAll && <span className="text-xs text-text-muted">还有问题没回答——没回答不等于回答了「否」，到不了 READY</span>}
          {evalM.isError && <span className="text-xs text-danger">检查失败：{(evalM.error as Error).message}</span>}
          {stale && <span className="text-xs text-warn">输入改过了，下面是改之前的结论——重新检查</span>}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <VerdictPanel decision={result.decision} asOf={result.as_of} mode={result.mode} savedId={result.id} />
          <CheckModules modules={result.modules} />
          {result.id != null && result.mode === 'HISTORICAL' && (
            outcomeQ.data ? <OutcomePanel o={outcomeQ.data} /> : (
              <button onClick={() => outcomeQ.refetch()} disabled={outcomeQ.isFetching}
                      className="rounded-lg border border-bg-border px-4 py-1.5 text-xs text-text-secondary hover:text-accent hover:border-accent/40 disabled:opacity-50">
                {outcomeQ.isFetching ? '取后续走势……' : '查看后续走势（不影响上面的判定）'}
              </button>
            )
          )}
        </div>
      )}

      {isLoggedIn && (histQ.data?.length ?? 0) > 0 && (
        <div className="card p-4 space-y-2">
          <div className="text-xs font-semibold text-text-primary">最近的检查</div>
          <div className="divide-y divide-bg-border/40">
            {histQ.data!.map((h) => (
              <button key={h.id} onClick={() => openSaved(h.id)}
                      className="flex w-full items-center gap-3 py-1.5 text-left text-xs hover:bg-bg-elevated/40">
                <span className="w-10 font-mono text-text-muted">#{h.id}</span>
                <span className="w-28 text-text-primary">{h.stock_name ?? h.stock_code}</span>
                <span className="w-40 font-mono text-text-muted">{h.as_of.replace('T', ' ')}</span>
                <span className="w-10 text-text-muted">{h.mode === 'LIVE' ? '实时' : '复盘'}</span>
                <span className={cn('font-semibold', VERDICT_TONE[h.verdict])}>{h.verdict}</span>
                <span className="ml-auto text-text-muted">{h.rule_version}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
