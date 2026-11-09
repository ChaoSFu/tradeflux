import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenCheck, Lock, Radio, Save } from 'lucide-react'
import {
  fetchRecapContext, fetchRecapDates, fetchRecapDay, fetchVerification, lockRecap, saveRecap,
  type Plan, type RecapDay, type RecapDraft,
} from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { CorePanel } from '@/components/marketRecap/CorePanel'
import { MarketPanel } from '@/components/marketRecap/MarketPanel'
import { PlanPanel, ScenarioPanel, emptyPlan } from '@/components/marketRecap/PlanPanel'
import { CorePoolPanel } from '@/components/marketRecap/CorePoolPanel'
import { LivePanel } from '@/components/marketRecap/LivePanel'
import { DecisionSummary, PreparePanel } from '@/components/marketRecap/PreparePanel'
import { RelationPanel } from '@/components/marketRecap/RelationPanel'
import { ReflectPanel, SummaryCard, VerificationCard, VersionList } from '@/components/marketRecap/ReviewPanel'
import { MetaBadge, Section } from '@/components/marketRecap/shared'
import { cn } from '@/utils/cn'

const NAV = [['market', '市场判断'], ['cores', '人气核心'], ['core', '主线与观察池'], ['relations', '关联研究'], ['plan', '次日预案'], ['review', '复盘总结']] as const
const MODES = [['research', '盘后研究'], ['prepare', '次日准备'], ['live', '盘中跟踪'], ['verify', '结果验证']] as const
type Mode = typeof MODES[number][0]
const isMode = (v: string | null): v is Mode => MODES.some(([m]) => m === v)

/** 工作日 09:15–15:00（上海时间）打开页面，默认进盘中跟踪。只是默认，是不是交易日以后端的行情时间为准 */
function defaultMode(): Mode {
  const sh = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
  const hm = sh.getHours() * 100 + sh.getMinutes()
  return sh.getDay() >= 1 && sh.getDay() <= 5 && hm >= 915 && hm < 1500 ? 'live' : 'research'
}

const emptyDraft = (): RecapDraft => ({
  market_posture: null, market_evidence: [], evidence_note: null, invalidation: [], sector_picks: [],
  scenarios: { A: {}, B: {}, C: {}, N: {} }, flat_plan: false, discovery: null, mistake: null,
  mistake_type: null, hypothesis: null, deviations: [], plans: [], key_points: [],
})

function fromDay(day: RecapDay): RecapDraft {
  const r = day.recap
  if (!r) return { ...emptyDraft(), plans: day.plans }
  return {
    market_posture: r.market_posture, market_evidence: r.market_evidence ?? [], evidence_note: r.evidence_note,
    invalidation: r.invalidation ?? [], sector_picks: r.sector_picks ?? [],
    scenarios: r.scenarios ?? emptyDraft().scenarios, flat_plan: r.flat_plan,
    discovery: r.discovery, mistake: r.mistake, mistake_type: r.mistake_type, hypothesis: r.hypothesis,
    deviations: r.deviations ?? [], plans: day.plans, key_points: r.key_points ?? [],
  }
}

export default function MarketRecap() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const { data: dates } = useQuery({ queryKey: ['recap-dates'], queryFn: fetchRecapDates })
  const date = params.get('date') ?? dates?.[0]?.date ?? null

  const modeParam = params.get('mode')
  const [mode, setModeState] = useState<Mode>(isMode(modeParam) ? modeParam : defaultMode)
  const setMode = (m: Mode) => {
    setModeState(m)
    setParams((p) => { const n = new URLSearchParams(p); n.set('mode', m); return n }, { replace: true })
  }
  const day = useQuery({ queryKey: ['recap-day', date], queryFn: () => fetchRecapDay(date!), enabled: !!date })
  // 盘中跟踪用不到选中日期的市场事实：不在交易时段里白算一份
  const ctx = useQuery({ queryKey: ['recap-context', date], queryFn: () => fetchRecapContext(date!), enabled: !!date && mode !== 'live' })
  const verifySelf = useQuery({
    queryKey: ['recap-verify', date], queryFn: () => fetchVerification(date!),
    enabled: !!date && mode !== 'live' && !!day.data?.locked_versions.length,
  })

  const [draft, setDraft] = useState<RecapDraft>(emptyDraft())
  const [saved, setSaved] = useState<string>(JSON.stringify(emptyDraft()))
  const [msg, setMsg] = useState<{ text: string; err?: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [leaderSel, setLeaderSel] = useState<string | null>(null)
  const [extraLeader, setExtraLeader] = useState<{ code: string; name: string | null } | null>(null)
  // 只在换日期 / 首次载入时用服务器的草稿覆盖本地；之后的重新拉取（比如加观察股）不冲掉没保存的修改
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  useEffect(() => {
    if (day.data && date && loadedFor !== date) {
      const d = fromDay(day.data)
      setDraft(d); setSaved(JSON.stringify(d)); setLoadedFor(date)
    }
  }, [day.data, date, loadedFor])
  const dirty = JSON.stringify(draft) !== saved
  const set = (patch: Partial<RecapDraft>) => setDraft((d) => ({ ...d, ...patch }))

  const switchDate = (d: string) => {
    if (dirty && !window.confirm('有没保存的修改，切换日期会丢掉。继续？')) return
    setParams({ date: d, mode }); setMsg(null)
  }
  const refreshDay = () => {
    qc.invalidateQueries({ queryKey: ['recap-day', date] })
    qc.invalidateQueries({ queryKey: ['recap-context', date] })
  }
  const save = async () => {
    if (!date) return false
    setBusy(true); setMsg(null)
    try {
      await saveRecap(date, draft)
      setSaved(JSON.stringify(draft))
      qc.invalidateQueries({ queryKey: ['recap-day', date] })
      qc.invalidateQueries({ queryKey: ['recap-dates'] })
      setMsg({ text: '草稿已保存' })
      return true
    } catch (e: any) {  // eslint-disable-line @typescript-eslint/no-explicit-any
      setMsg({ text: e?.message ?? '保存失败', err: true })
      return false
    } finally { setBusy(false) }
  }
  const research = (code: string, name: string | null) => {
    setLeaderSel(code)
    if (!day.data?.watch_pool.some((w) => w.code === code)) setExtraLeader({ code, name })
    document.getElementById('relations')?.scrollIntoView({ behavior: 'smooth' })
  }
  // 「拟交易」只是放进草稿：入场 / 失效条件还得自己写，保存时后端会拦
  const addPlan = (p: Partial<Plan>) => {
    if (draft.flat_plan) { setMsg({ text: '现在是空仓预案，先取消空仓再加候选', err: true }); return }
    if (draft.plans.some((x) => x.stock_code === p.stock_code)) { setMsg({ text: `${p.stock_code} 已经在预案里`, err: true }); return }
    if (draft.plans.length >= 5) { setMsg({ text: '次日预案最多 5 只', err: true }); return }
    set({ plans: [...draft.plans, { ...emptyPlan(), ...p }] })
    setMsg({ text: `${p.stock_name ?? p.stock_code} 已放进次日预案（草稿），去写它自己的入场和失效条件` })
  }
  const lock = async () => {
    if (!date) return
    if (!window.confirm('锁定后这一版不能再改；之后修改草稿要重新锁定，生成新版本。\n锁定时间晚于下一交易日开盘的会标成「事后记录」。确定锁定？')) return
    if (dirty && !(await save())) return
    setBusy(true)
    try {
      const v = await lockRecap(date)
      qc.invalidateQueries({ queryKey: ['recap-day', date] })
      qc.invalidateQueries({ queryKey: ['recap-dates'] })
      qc.invalidateQueries({ queryKey: ['recap-verify', date] })
      setMsg({ text: `已锁定 v${v.version}（${v.pre_open ? '盘前' : '事后记录'}）` })
    } catch (e: any) {  // eslint-disable-line @typescript-eslint/no-explicit-any
      setMsg({ text: e?.message ?? '锁定失败', err: true })
    } finally { setBusy(false) }
  }

  const modeBar = (
    <div className="inline-flex rounded border border-bg-border overflow-hidden mr-2 text-xs">
      {MODES.map(([m, l]) => (
        <button key={m} onClick={() => setMode(m)}
                className={cn('px-2 py-0.5 inline-flex items-center gap-1', mode === m ? 'bg-purple-400/15 text-purple-200' : 'text-text-muted hover:text-text-secondary')}>
          {m === 'live' && <Radio className="w-3 h-3" />}{l}
        </button>
      ))}
    </div>
  )
  // 盘中跟踪不依赖选中的复盘日期，也不依赖那天的市场事实——单独渲染，那边读取失败不拦着这边
  if (mode === 'live') {
    return (
      <div className="space-y-3 animate-fade-in">
        <div className="card p-3 sticky top-0 z-20 flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <BookOpenCheck className="w-4 h-4 text-accent" />
            <h1 className="text-base font-semibold text-text-primary">每日复盘</h1>
          </div>
          {modeBar}
          {dirty && <span className="text-[11px] text-warn">{date} 的草稿有没保存的修改：切回「盘后研究」保存</span>}
        </div>
        <LivePanel onOpenPrepare={(d) => { setParams({ date: d, mode: 'prepare' }); setModeState('prepare') }} />
      </div>
    )
  }
  if (!dates) return <LoadingSpinner />
  if (!dates.length) return <div className="card p-6 text-sm text-text-muted">还没有可以复盘的交易日（需要已收盘、有股票日快照）。</div>
  if (!date || day.isLoading || ctx.isLoading) return <LoadingSpinner />
  if (day.isError || ctx.isError) {
    const e = (day.error ?? ctx.error) as any  // eslint-disable-line @typescript-eslint/no-explicit-any
    return <div className="card p-6 text-sm text-down">读取失败：{e?.message ?? String(e)}</div>
  }
  const d = day.data!, c = ctx.data!
  const lastLock = d.locked_versions[d.locked_versions.length - 1]

  return (
    <div className="space-y-3 animate-fade-in">
      <div className="card p-3 space-y-2 sticky top-0 z-20">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <BookOpenCheck className="w-4 h-4 text-accent" />
            <h1 className="text-base font-semibold text-text-primary">每日复盘</h1>
            <select value={date} onChange={(e) => switchDate(e.target.value)}
                    className="bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 text-sm text-text-primary font-mono">
              {dates.map((x) => (
                <option key={x.date} value={x.date}>
                  {x.date}{x.locked_versions ? ` · 已锁定 ${x.locked_versions} 版` : x.recap_status ? ' · 草稿' : ''}
                </option>
              ))}
            </select>
            {lastLock
              ? <span className="text-[11px] px-1.5 py-0.5 rounded bg-up/10 text-up">已锁定 v{lastLock.version}{lastLock.pre_open ? '' : '（事后）'}</span>
              : d.recap ? <span className="text-[11px] px-1.5 py-0.5 rounded bg-bg-elevated text-text-secondary">草稿</span>
                : <span className="text-[11px] px-1.5 py-0.5 rounded bg-bg-elevated text-text-muted">未开始</span>}
            {dirty && <span className="text-[11px] text-warn">未保存</span>}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            {msg && <span className={cn('text-xs', msg.err ? 'text-down' : 'text-text-secondary')}>{msg.text}</span>}
            <button onClick={save} disabled={busy || !dirty}
                    className={cn('inline-flex items-center gap-1 px-3 py-1 rounded text-xs border',
                      dirty ? 'border-accent/50 text-accent hover:bg-accent/10' : 'border-bg-border text-text-muted')}>
              <Save className="w-3.5 h-3.5" />保存草稿
            </button>
            <button onClick={lock} disabled={busy}
                    className="inline-flex items-center gap-1 px-3 py-1 rounded text-xs bg-purple-500/80 text-white hover:bg-purple-500">
              <Lock className="w-3.5 h-3.5" />锁定次日预案
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          {modeBar}
          {mode === 'research' && NAV.map(([id, label], i) => (
            <a key={id} href={`#${id}`} className="px-2 py-0.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated">
              {'①②③④⑤⑥'[i]} {label}
            </a>
          ))}
          {c.status === 'ok' && (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-text-muted">
              数据状态：{Object.entries(c.data_status).map(([k, m]) => (
                <span key={k} className="inline-flex items-center gap-0.5">{({ indices: '指数', breadth: '广度', amount: '成交', feedback: '接力', sectors: '板块', candidates: '候选' } as Record<string, string>)[k]}<MetaBadge meta={m} /></span>
              ))}
            </span>
          )}
        </div>
      </div>

      {mode === 'prepare' && <PreparePanel date={date} />}

      {mode === 'verify' && (
        <div className="space-y-3">
          {verifySelf.data && verifySelf.data.status !== 'no_lock' ? (
            <div className="card p-3">
              <div className="text-xs font-medium text-text-primary mb-1.5">{date} 锁定的预案 → 下一交易日实际</div>
              <VerificationCard v={verifySelf.data} recapDate={date} />
            </div>
          ) : <div className="card p-6 text-sm text-text-muted">{date} 没有锁定的预案，没法验证。</div>}
          {d.prev_verification && d.prev_verification.status !== 'no_lock' && d.prev_date && (
            <div className="card p-3">
              <div className="text-xs font-medium text-text-primary mb-1.5">上一交易日（{d.prev_date}）锁定的预案 → {date} 实际</div>
              <VerificationCard v={d.prev_verification} recapDate={d.prev_date} />
            </div>
          )}
          <div className="card p-3"><VersionList date={date} versions={d.locked_versions} /></div>
        </div>
      )}

      {mode === 'research' && (c.status !== 'ok' ? (
        <div className="card p-6 text-sm text-text-muted">{c.message}</div>
      ) : (
        <>
          <DecisionSummary date={date} draft={draft} set={set} summary={d.summary} pool={d.watch_pool} />
          {d.prev_verification && d.prev_verification.status !== 'no_lock' && d.prev_date && (
            <div className="card p-3 border border-purple-400/30">
              <div className="text-xs font-medium text-text-primary mb-1.5">上一交易日锁定的预案（盘前准备 / 今日验证）</div>
              <VerificationCard v={d.prev_verification} recapDate={d.prev_date} />
            </div>
          )}
          <Section id="market" step="①" title={`市场状态 · ${date}`} right={<span className="text-[11px] text-text-muted">系统给事实，姿态由你判断</span>}>
            <MarketPanel ctx={c} draft={draft} set={set} readOnly={false} />
          </Section>
          <Section id="cores" step="②" title="市场人气核心池" right={<span className="text-[11px] text-text-muted">多入口召回，不打分；从这里挑 5~8 只观察、3~5 只深入</span>}>
            <CorePoolPanel date={date} onResearch={research} onPoolChanged={refreshDay} />
          </Section>
          <Section id="core" step="③" title="主线板块与我的观察池">
            <CorePanel ctx={c} draft={draft} set={set} pool={d.watch_pool} date={date} readOnly={false} onPoolChanged={refreshDay} />
          </Section>
          <Section id="relations" step="④" title="高标关联研究" right={<span className="text-[11px] text-text-muted">关联、响应即时保存（不随草稿）</span>}>
            <RelationPanel pool={d.watch_pool} date={date} readOnly={false} selected={leaderSel} onSelect={setLeaderSel}
                           extraLeader={extraLeader} onAddPlan={addPlan} onPoolChanged={refreshDay} />
          </Section>
          <Section id="plan" step="⑤" title="次日情景与交易预案">
            <ScenarioPanel draft={draft} set={set} pool={d.watch_pool} readOnly={false} />
            <PlanPanel draft={draft} set={set} pool={d.watch_pool} readOnly={false} />
          </Section>
          <Section id="review" step="⑥" title="认知复盘与最终结果">
            <ReflectPanel draft={draft} set={set} readOnly={false} />
            <SummaryCard s={d.summary} dirty={dirty} />
            <VersionList date={date} versions={d.locked_versions} />
          </Section>
        </>
      ))}
    </div>
  )
}
