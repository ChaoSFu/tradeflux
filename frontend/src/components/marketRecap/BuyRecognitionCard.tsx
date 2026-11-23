import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, ArrowRight } from 'lucide-react'
import type { BrItem, BrMainline, BuyRecognition } from '@/api/marketRecap'
import { NUM, StockLink, fmtPct, pctCls } from './shared'
import { cn } from '@/utils/cn'

/**
 * 买入识别（buy_recog_v1，2026-11-23）——服务龙头弱转强交易。
 *
 * 默认三行：市场过关 / 主线走强 / 带队个股；点开看每一条的证据（来源、行情时间、覆盖、判定原因）。
 * **规则全在后端**（buy_recognition_rules.py），这里只摆结论和证据，不在前端再判一遍。
 * 「进入买入检查」只在市场过关 + 主线确认 + 带队确认时出现，带上股票代码和该主线的板块 id；
 * 实际买点、空间、止损、仓位由买入检查继续核对。盘后研究只摆收盘事实，不给入口。
 */

// 红绿灯语义走 safe / danger（本项目 up=红、down=绿 是涨跌色，不能拿来表示「过 / 拦」）
const TONE: Record<string, string> = {
  PASS: 'bg-safe/15 text-safe border-safe/30', CONFIRMED: 'bg-safe/15 text-safe border-safe/30',
  BLOCK: 'bg-danger/15 text-danger border-danger/30',
  NOT_MET: 'bg-warn/10 text-warn border-warn/30', PENDING: 'bg-warn/10 text-warn border-warn/30',
  CANDIDATE: 'bg-warn/10 text-warn border-warn/30', SAME: 'bg-warn/10 text-warn border-warn/30',
  INSUFFICIENT: 'bg-bg-elevated text-text-muted border-bg-border', NONE: 'bg-bg-elevated text-text-muted border-bg-border',
  CLOSE: 'bg-bg-elevated text-text-secondary border-bg-border',
}
const ORIGIN_ZH: Record<string, string> = { continuation: '延续', repair: '分歧后修复', new: '新方向' }
const yi = (v: number | null | undefined) => (v == null ? '—' : `${(v / 1e8).toFixed(v >= 1e10 ? 0 : 2)}亿`)

function Pill({ status, children, title }: { status: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn('inline-flex items-center shrink-0 px-1.5 py-px rounded border text-[11px] font-medium whitespace-nowrap', TONE[status] ?? TONE.INSUFFICIENT)}>
      {children}
    </span>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] gap-2 items-start">
      <span className="text-[11px] text-text-muted pt-0.5">{label}</span>
      <div className="min-w-0 space-y-1">{children}</div>
    </div>
  )
}

function Meta({ meta }: { meta?: BrItem['meta'] }) {
  if (!meta) return null
  const bits = [meta.source, meta.observed_at && `行情时间 ${meta.observed_at}`, meta.trade_date && `交易日 ${meta.trade_date}`, meta.coverage]
    .filter(Boolean)
  return <div className="text-[10px] text-text-muted/80">{bits.join(' · ')}</div>
}

// ── 展开后的证据 ─────────────────────────────────────────────────────────────

function ItemEvidence({ it }: { it: BrItem }) {
  const [names, setNames] = useState(false)
  return (
    <div className="space-y-1 border-l-2 border-bg-border pl-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-text-primary font-medium">{it.title}</span>
        <Pill status={it.status}>{it.status_zh} · {it.label}</Pill>
      </div>
      <div className="text-[11px] text-text-secondary">{it.reason}</div>
      <Meta meta={it.meta} />
      {it.key === 'limit_down' && !!it.names?.length && (
        <div className="text-[11px]">
          <button onClick={() => setNames(!names)} className="text-accent">{names ? '收起' : '展开'}个股名单（{it.names.length}）</button>
          {names && (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-text-secondary">
              {it.names.map((n) => (
                <span key={n.code} title={`封单 ${yi(n.seal_amount)} · 连续跌停 ${n.down_days ?? '—'} 天 · 开板 ${n.open_times ?? '—'} 次 · 最后封板 ${n.last_seal_time ?? '—'}`}>
                  <StockLink code={n.code} name={n.name} />
                  <span className={cn('ml-0.5', NUM)}>{yi(n.seal_amount)}</span>
                  {n.verified === 'missing' && <span className="text-warn">（没核上）</span>}
                </span>
              ))}
            </div>
          )}
          {it.meta?.compare_error && <div className="text-[10px] text-warn">对照口径没拿到：{it.meta.compare_error}</div>}
        </div>
      )}
      {it.key === 'bear' && (
        <div className="space-y-0.5 text-[11px]">
          {(it.targets ?? []).map((t) => (
            <div key={t.code} className="flex flex-wrap items-baseline gap-x-2">
              <StockLink code={t.code} name={t.name} />
              <span className={cn(NUM, pctCls(t.pct))}>{fmtPct(t.pct)}</span>
              <Pill status={t.judge.state === 'STOPPED' ? 'PASS' : ['SEALED', 'RESEALED', 'NEW_LOW'].includes(t.judge.state) ? 'BLOCK' : t.judge.state === 'TESTING' ? 'NOT_MET' : 'INSUFFICIENT'}>
                {t.judge.state_zh}
              </Pill>
              <span className="text-text-muted">锁定：{t.basis}</span>
              {t.judge.evidence && <span className="text-text-secondary">{t.judge.evidence}</span>}
            </div>
          ))}
          {(it.notes ?? []).map((n) => <div key={n} className="text-[10px] text-text-muted/80">{n}</div>)}
        </div>
      )}
      {it.key === 'index' && (
        <div className="space-y-0.5 text-[11px]">
          {(it.indices ?? []).map((x) => (
            <div key={x.code} className="flex flex-wrap gap-x-2">
              <span className="text-text-primary w-16 shrink-0">{x.name}</span>
              <span className={cn(NUM, pctCls(x.judge.pct))}>{fmtPct(x.judge.pct)}</span>
              <span className={x.judge.ok ? 'text-safe' : x.judge.ok === false ? 'text-warn' : 'text-text-muted'}>{x.judge.evidence}</span>
            </div>
          ))}
          {it.volume ? (
            <div className="text-text-secondary">
              同期比 <span className={NUM}>{it.volume.ratio.toFixed(2)}</span>：此刻累计 {yi(it.volume.amount)} vs {it.volume.prev_date ?? '上一交易日'} 同期 {yi(it.volume.prev_same)}（比较时刻 {it.volume.same_as_of ?? '—'}，{it.volume.source}）
            </div>
          ) : it.volume_error ? <div className="text-warn">同期成交额：{it.volume_error}</div> : null}
          {it.breadth?.up_ratio != null && (
            <div className="text-text-secondary">涨跌家数 {it.breadth.up} / {it.breadth.down}，上涨占 {(it.breadth.up_ratio * 100).toFixed(0)}%</div>
          )}
          {it.closes && (
            <div className="text-text-secondary">
              收盘：{it.closes.map((c) => <span key={c.code} className={cn('mr-2', NUM, pctCls(c.pct))}>{c.code} {fmtPct(c.pct)}</span>)}
              成交额 {yi(it.amount)}（上一交易日 {yi(it.prev_amount)}，全天对全天，不是同期）
            </div>
          )}
        </div>
      )}
      {it.key === 'highmark' && (
        <div className="space-y-0.5 text-[11px]">
          {it.scope && <div className="text-[10px] text-text-muted/80">样本：{it.scope}</div>}
          {!!it.sample?.length && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              {it.sample.map((x) => (
                <span key={x.code}>
                  <StockLink code={x.code} name={x.name} />
                  <span className="text-[10px] text-text-muted ml-0.5">{x.state_zh}</span>
                  <span className={cn('ml-1', NUM, pctCls(x.pct))}>{fmtPct(x.pct)}</span>
                  {x.limit === 'limit_down' && <span className="text-danger ml-0.5">跌停</span>}
                </span>
              ))}
            </div>
          )}
          {it.memory_note && <div className="text-[10px] text-text-muted/80">{it.memory_note}</div>}
        </div>
      )}
    </div>
  )
}

function MainlineEvidence({ m }: { m: BrMainline }) {
  const s = m.series
  return (
    <div className="space-y-1 border-l-2 border-bg-border pl-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-text-primary font-medium">{m.sector.name}</span>
        {m.origin && <span className="text-[10px] text-text-muted">{ORIGIN_ZH[m.origin]}{m.prev_state ? `（昨日 ${m.prev_state}）` : ''}</span>}
        <Pill status={m.status}>{m.label}</Pill>
        {!!m.merged.length && <span className="text-[10px] text-text-muted" title="今天活跃成分重合 ≥60%，按成员归并">并：{m.merged.join('、')}</span>}
      </div>
      <div className="text-text-secondary">{m.reason}</div>
      {s && (
        <div className="text-text-muted">
          板块分时：低点 {s.sec_low_at}（上证 {s.idx_low_at}），此刻 {fmtPct(s.sec_pct)} vs 上证 {fmtPct(s.idx_pct)}，跑赢 {s.rs_now.toFixed(2)}；
          {s.had_pullback ? `回踩 ${s.pullback_pp.toFixed(2)} 个百分点，${s.rs_held ? '期间一直跑赢' : '期间跑输过'}，${s.restrength ? '已再度走强' : '还没再走强'}` : '还没回踩'}
        </div>
      )}
      {!s && m.series_error && <div className="text-warn">板块分时：{m.series_error}</div>}
      {m.breadth && (
        <div className="text-text-muted">
          成分 {m.breadth.members} 只（行情 {m.breadth.quoted}）：上涨 {m.breadth.up}、下跌 {m.breadth.down}，中位 {fmtPct(m.breadth.median_pct)}，
          跟进 {m.breadth.strong} 只{m.breadth.strong_names.length ? `（${m.breadth.strong_names.join('、')}）` : ''}
        </div>
      )}
      {m.leader && (
        <div className="space-y-0.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-text-muted">带队</span><Pill status={m.leader.status}>{m.leader.label}</Pill>
            <span className="text-text-secondary">{m.leader.reason}</span>
          </div>
          {!!m.leader_candidates?.length && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-text-muted">
              候选：{m.leader_candidates.map((c) => (
                <span key={c.code} title={[c.lead_basis, c.support_txt, c.in_core ? '昨日人气核心' : '', c.lu20 ? `近20日涨停 ${c.lu20}` : '',
                  c.board ? `${c.board} 板` : '', c.float_mv ? `流通 ${yi(c.float_mv)}` : ''].filter(Boolean).join(' · ')}>
                  <StockLink code={c.code} name={c.name} /> <span className={NUM}>{c.lead_at ?? '—'}</span>
                  <span className={cn('ml-0.5', NUM, pctCls(c.pct))}>{fmtPct(c.pct)}</span>
                </span>
              ))}
            </div>
          )}
          {m.leader_note && <div className="text-[10px] text-text-muted/80">{m.leader_note}</div>}
        </div>
      )}
    </div>
  )
}

// ── 卡片 ─────────────────────────────────────────────────────────────────────

export function BuyRecognitionCard({ data, loading, error }: { data?: BuyRecognition | null; loading?: boolean; error?: string | null }) {
  const [open, setOpen] = useState(false)
  if (loading && !data) return <div className="card p-3 text-xs text-text-muted">买入识别读取中…</div>
  if (error || !data || data.error) {
    return <div className="card p-3 text-xs text-warn">买入识别：{error ?? data?.error ?? '没有数据'}</div>
  }
  const m = data.market
  const top = data.mainlines[0]
  const close = data.mode === 'close'
  const withLeader = data.mainlines.find((x) => x.leader?.leader) ?? top
  const ld = withLeader?.leader
  return (
    <div className="card p-3 space-y-2 border border-purple-400/25">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm font-semibold text-text-primary">买入识别</span>
        <span className="text-[11px] text-text-muted">龙头弱转强 · 只回答该不该去买入检查</span>
        <span className={cn('text-[10px] px-1 rounded border border-dashed', close ? 'border-bg-border text-text-muted' : 'border-warn/50 text-warn')}>
          {close ? `${data.trade_date} 收盘事实` : '盘中临时'}
        </span>
        {data.as_of && <span className="text-[10px] text-text-muted">行情时间 <span className={NUM}>{data.as_of.slice(11, 19)}</span></span>}
        <button onClick={() => setOpen(!open)} className="ml-auto inline-flex items-center gap-0.5 text-[11px] text-accent">
          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}{open ? '收起证据' : '展开证据'}
        </button>
      </div>

      <Row label="市场过关">
        <div className="flex items-start gap-1.5">
          <Pill status={m.status}>{m.status === 'PASS' ? '市场过关' : m.label ?? m.status_zh}</Pill>
          <span className="text-[11px] text-text-secondary">{m.reason}</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {m.items.map((it) => (
            <span key={it.key} title={it.reason} className={cn('text-[10px] px-1 rounded border', TONE[it.status])}>
              {it.title} · {it.key === 'limit_down' && it.count != null ? `${it.count} 家` : it.label}
            </span>
          ))}
        </div>
      </Row>

      <Row label="主线走强">
        {!top ? <span className="text-[11px] text-text-muted">{data.mainline_note ?? '没有候选主线'}</span> : (
          <>
            <div className="flex flex-wrap items-start gap-1.5">
              <Pill status={top.status}>{top.label}</Pill>
              <span className="text-xs text-text-primary">{top.sector.name}</span>
              {top.origin && <span className="text-[10px] text-text-muted">{ORIGIN_ZH[top.origin]}</span>}
              {!!top.merged.length && <span className="text-[10px] text-text-muted">并：{top.merged.join('、')}</span>}
              <span className="text-[11px] text-text-secondary basis-full sm:basis-auto">{top.reason}</span>
            </div>
            {data.mainlines.length > 1 && (
              <div className="flex flex-wrap gap-1">
                {data.mainlines.slice(1).map((x) => (
                  <span key={x.sector.code} title={x.reason} className={cn('text-[10px] px-1 rounded border', TONE[x.status])}>
                    {x.sector.name} · {x.label}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </Row>

      <Row label="带队个股">
        {!ld?.leader ? <span className="text-[11px] text-text-muted">{ld?.reason || '暂无确认带队股'}</span> : (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <Pill status={ld.status}>{ld.label}</Pill>
            <StockLink code={ld.leader.code} name={ld.leader.name} />
            <span className="text-[10px] text-text-muted">{withLeader.sector.name}</span>
            {ld.leader.lead_at && <span className="text-[11px] text-text-secondary">{ld.leader.lead_at} 先动</span>}
            {!!ld.followers.length && (
              <span className="text-[11px] text-text-muted">
                跟进 {ld.followers.map((f) => `${f.name} ${f.at.slice(0, 5)}`).join('、')}
                {ld.followers_total && ld.followers_total > ld.followers.length ? ` 等 ${ld.followers_total} 只` : ''}
              </span>
            )}
          </div>
        )}
      </Row>

      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-bg-border/60">
        {data.entry ? (
          <Link to={`/pre-trade-check?code=${data.entry.code}&sector_id=${data.entry.sector_id}`}
                className="inline-flex items-center gap-1 px-2 py-1 rounded bg-purple-400/20 text-purple-100 text-xs hover:bg-purple-400/30">
            进入买入检查：{data.entry.name ?? data.entry.code} · {data.entry.sector_name}<ArrowRight className="w-3.5 h-3.5" />
          </Link>
        ) : (
          <span className="text-[11px] text-text-muted">没有买入检查入口：{data.entry_reason}</span>
        )}
        <span className="ml-auto text-[10px] text-text-muted/70">{data.rule_version} · 实际买点、空间、止损、仓位由买入检查继续核对</span>
      </div>

      {open && (
        <div className="space-y-3 pt-2 border-t border-bg-border/60">
          <div className="space-y-2">
            <div className="text-[11px] text-text-muted">市场过关：四项都满足才过关；有拦截先报拦截，其余分「条件未满足」和「数据不足」</div>
            {m.items.map((it) => <ItemEvidence key={it.key} it={it} />)}
          </div>
          <div className="space-y-2">
            <div className="text-[11px] text-text-muted">
              主线走强：要同一轮行情里三项证据——先于或伴随指数走强且跑赢上证、回踩后相对强度仍在、成分股有广度和多只独立跟进
              {data.prev_mainline_date && `；旧主线按 ${data.prev_mainline_date} 收盘的主升雷达`}
            </div>
            {data.mainlines.length ? data.mainlines.map((x) => <MainlineEvidence key={x.sector.code} m={x} />)
              : <div className="text-[11px] text-text-muted">{data.mainline_note}</div>}
          </div>
          {(data.notes?.length || data.memory_note) && (
            <div className="text-[10px] text-text-muted/80 space-y-0.5">
              {(data.notes ?? []).map((n) => <div key={n}>{n}</div>)}
              {data.memory_note && <div>{data.memory_note}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
