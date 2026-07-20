/**
 * 交易复盘日志（P1：记录与镜子）
 * 记录每一笔操作(建仓/加仓/减仓/卖出/清仓)，自动带入交易当下的市场环境快照。
 * 个人私有数据，登录后可用。后续 P2 在此数据上做检测引擎与行为画像。
 */
import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchTradeJournal, createTradeEntry, deleteTradeEntry, type TradeJournalPayload } from '@/api/tradeJournal'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { LoginModal } from '@/components/auth/LoginModal'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import { Lock, Plus, Trash2, BookOpen, AlertTriangle } from 'lucide-react'
import type { TradeAction, EmotionTag, ExitReason, TradeJournalEntry } from '@/types'

const ACTIONS: TradeAction[] = ['买入', '卖出']
const EXIT_ACTIONS = new Set<TradeAction>(['卖出'])
const EMOTIONS: EmotionTag[] = ['计划内', '抄底做T', '逆势加仓', '回本补救', '追高', '其他']
const EXIT_REASONS: ExitReason[] = ['止损', '恐慌', '反弹跑', '目标达成', '其他']

// 情绪标签配色：计划内=中性，其余情绪单=警示色（一眼区分理性 vs 冲动）
const EMOTION_STYLE: Record<EmotionTag, string> = {
  '计划内': 'text-accent bg-accent/10 border-accent/30',
  '抄底做T': 'text-warn bg-warn/10 border-warn/30',
  '逆势加仓': 'text-up bg-up/10 border-up/30',
  '回本补救': 'text-up bg-up/10 border-up/30',
  '追高': 'text-warn bg-warn/10 border-warn/30',
  '其他': 'text-text-muted bg-bg-elevated border-bg-border',
}

const PHASE_ZH: Record<string, string> = {
  bull_frenzy: '狂热', warm: '偏暖', neutral: '中性', caution: '谨慎', bear_fear: '恐慌',
}

const nowLocal = () => {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

export default function TradeJournal() {
  const isLoggedIn = useAuthStore(s => s.isLoggedIn)
  const [showLogin, setShowLogin] = useState(false)

  if (!isLoggedIn) {
    return (
      <div className="animate-fade-in">
        {showLogin && <LoginModal onClose={() => setShowLogin(false)} />}
        <div className="card max-w-md mx-auto mt-16 p-8 text-center">
          <div className="w-12 h-12 rounded-xl bg-accent/15 border border-accent/25 flex items-center justify-center mx-auto">
            <Lock className="w-6 h-6 text-accent" />
          </div>
          <h2 className="text-lg font-semibold text-text-primary mt-4">交易复盘 · 个人私有</h2>
          <p className="text-sm text-text-muted mt-2 leading-relaxed">
            记录你的每一笔操作,让系统在成百上千笔里发现你反复在犯的错。数据仅你自己可见,登录后使用。
          </p>
          <button
            onClick={() => setShowLogin(true)}
            className="mt-5 px-4 py-2 rounded-lg bg-accent/15 border border-accent/30 text-accent text-sm hover:bg-accent/25 transition-colors"
          >
            登录后开始记录
          </button>
        </div>
      </div>
    )
  }
  return <Journal />
}

function Journal() {
  const qc = useQueryClient()
  const [stockFilter, setStockFilter] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['trade-journal', stockFilter],
    queryFn: () => fetchTradeJournal({ stock: stockFilter || undefined, page_size: 200 }),
  })

  const del = useMutation({
    mutationFn: (id: number) => deleteTradeEntry(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['trade-journal'] }),
  })

  const items = data?.items ?? []

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center gap-2 text-xs text-text-muted">
        <BookOpen className="w-3.5 h-3.5 text-accent" />
        记录每一笔操作与买入理由;交易当下的市场环境会自动留存,用于后续复盘发现你的行为问题。
      </div>

      {/* ── 汇总条 ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <SummaryCard label="记录笔数" value={data?.total ?? 0} />
        <SummaryCard
          label="已实现盈亏合计"
          value={data ? `${data.realized_pnl_sum >= 0 ? '+' : ''}¥${data.realized_pnl_sum.toLocaleString()}` : '—'}
          color={data ? (data.realized_pnl_sum >= 0 ? 'up' : 'down') : undefined}
        />
        <SummaryCard label="盈 / 亏 (平仓笔)" value={data ? `${data.win_count} / ${data.loss_count}` : '—'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,360px)_1fr] gap-4 items-start">
        {/* ── 录入表单 ───────────────────────────────────────── */}
        <EntryForm onDone={() => qc.invalidateQueries({ queryKey: ['trade-journal'] })} />

        {/* ── 交易列表 ───────────────────────────────────────── */}
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 border-b border-bg-border/40 flex items-center gap-2">
            <span className="text-sm font-semibold text-text-primary">交易记录</span>
            <input
              value={stockFilter}
              onChange={(e) => setStockFilter(e.target.value)}
              placeholder="按代码/名称筛选…"
              className="ml-auto bg-bg-elevated border border-bg-border rounded-lg px-2.5 py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 w-40"
            />
          </div>
          {isLoading ? (
            <div className="p-4"><LoadingRows /></div>
          ) : items.length === 0 ? (
            <div className="py-16 text-center text-text-muted text-sm">还没有记录,从左侧添加第一笔</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border/40">
                    {['时间', '股票', '方向', '价格', '仓位', '情绪', '理由 / 触发', '盈亏', '当时环境', ''].map(h => (
                      <th key={h} className="px-2.5 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((t) => <Row key={t.id} t={t} onDelete={() => del.mutate(t.id)} />)}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Row({ t, onDelete }: { t: TradeJournalEntry; onDelete: () => void }) {
  const isExit = EXIT_ACTIONS.has(t.action)
  const actionColor = t.action === '买入' ? 'text-up' : 'text-down'
  return (
    <tr className="border-b border-bg-border/20 hover:bg-bg-elevated/40">
      <td className="px-2.5 py-2 font-mono text-text-muted whitespace-nowrap">{format(new Date(t.trade_time), 'MM-dd HH:mm')}</td>
      <td className="px-2.5 py-2 whitespace-nowrap">
        <span className="text-text-primary">{t.stock_name ?? '—'}</span>
        <span className="text-text-muted/70 font-mono ml-1">{t.stock_code}</span>
      </td>
      <td className={cn('px-2.5 py-2 font-medium whitespace-nowrap', actionColor)}>{t.action}</td>
      <td className="px-2.5 py-2 font-mono text-text-secondary">{t.price}</td>
      <td className="px-2.5 py-2 font-mono text-text-muted">{t.position_pct != null ? `${t.position_pct}%` : '—'}</td>
      <td className="px-2.5 py-2 whitespace-nowrap">
        {t.emotion_tag ? (
          <span className={cn('text-[10px] font-bold px-1.5 py-0.5 rounded border', EMOTION_STYLE[t.emotion_tag])}>{t.emotion_tag}</span>
        ) : '—'}
      </td>
      <td className="px-2.5 py-2 text-text-secondary max-w-[180px] truncate" title={isExit ? (t.exit_reason ?? '') : (t.reason ?? '')}>
        {isExit
          ? (t.exit_reason ? <span className="text-warn">{t.exit_reason}</span> : '—')
          : (t.reason ?? '—')}
        {!isExit && t.planned_stop != null && <span className="text-text-muted/70 ml-1.5 font-mono">止损{t.planned_stop}</span>}
      </td>
      <td className="px-2.5 py-2 font-mono whitespace-nowrap">
        {t.realized_pnl != null ? (
          <span className={t.realized_pnl >= 0 ? 'text-up' : 'text-down'}>
            {t.realized_pnl >= 0 ? '+' : ''}{t.realized_pnl.toLocaleString()}
            {t.pnl_pct != null && <span className="opacity-70 ml-1">{t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%</span>}
          </span>
        ) : '—'}
      </td>
      <td className="px-2.5 py-2 whitespace-nowrap text-text-muted">
        {t.mkt_phase ? (
          <span title={`温度 ${t.mkt_temperature} · 建议仓位 ${t.mkt_suggested_position}%`}>
            {PHASE_ZH[t.mkt_phase] ?? t.mkt_phase}
            {t.mkt_suggested_position != null && <span className="font-mono ml-1 opacity-70">{t.mkt_suggested_position}%</span>}
          </span>
        ) : '—'}
      </td>
      <td className="px-2.5 py-2">
        <button onClick={onDelete} className="p-1 rounded text-text-muted hover:text-down hover:bg-bg-elevated transition-colors" title="删除">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </td>
    </tr>
  )
}

function EntryForm({ onDone }: { onDone: () => void }) {
  const [action, setAction] = useState<TradeAction>('买入')
  const [f, setF] = useState({
    stock_code: '', stock_name: '', trade_time: nowLocal(), price: '', position_pct: '',
    reason: '', planned_stop: '', target: '', emotion_tag: '' as '' | EmotionTag, note: '',
    exit_reason: '' as '' | ExitReason, realized_pnl: '', pnl_pct: '',
  })
  const [err, setErr] = useState<string | null>(null)
  const isExit = EXIT_ACTIONS.has(action)

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF(prev => ({ ...prev, [k]: e.target.value }))

  const num = (v: string) => (v.trim() === '' ? null : Number(v))

  const create = useMutation({
    mutationFn: (body: TradeJournalPayload) => createTradeEntry(body),
    onSuccess: () => {
      setF(p => ({ ...p, stock_code: '', stock_name: '', price: '', position_pct: '', reason: '',
        planned_stop: '', target: '', note: '', realized_pnl: '', pnl_pct: '', trade_time: nowLocal() }))
      setErr(null)
      onDone()
    },
    onError: (e: any) => setErr(e.message ?? '提交失败'),
  })

  const submit = () => {
    if (!f.stock_code.trim() && !f.stock_name.trim()) return setErr('股票代码或名称,至少填一个')
    if (!f.price.trim()) return setErr('请填价格')
    // 事前摩擦：建仓/加仓必须写理由与止损
    if (!isExit && !f.reason.trim()) return setErr('买入必须写理由——这是纪律的第一道闸')
    if (!isExit && !f.planned_stop.trim()) return setErr('买入必须写计划止损价')
    create.mutate({
      stock_code: f.stock_code.trim(), stock_name: f.stock_name.trim() || null,
      action, trade_time: f.trade_time, price: Number(f.price), position_pct: num(f.position_pct),
      reason: isExit ? null : (f.reason.trim() || null),
      planned_stop: isExit ? null : num(f.planned_stop),
      target: isExit ? null : num(f.target),
      emotion_tag: f.emotion_tag || null, note: f.note.trim() || null,
      exit_reason: isExit ? (f.exit_reason || null) : null,
      realized_pnl: isExit ? num(f.realized_pnl) : null,
      pnl_pct: isExit ? num(f.pnl_pct) : null,
    })
  }

  const inputCls = 'w-full bg-bg-elevated border border-bg-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50'
  const labelCls = 'text-[11px] text-text-muted mb-1 block'

  return (
    <div className="card p-4 space-y-3 lg:sticky lg:top-2">
      <div className="flex items-center gap-2">
        <Plus className="w-4 h-4 text-accent" />
        <span className="text-sm font-semibold text-text-primary">记一笔操作</span>
      </div>

      {/* 方向 */}
      <div className="flex gap-1">
        {ACTIONS.map(a => (
          <button key={a} onClick={() => setAction(a)}
            className={cn('flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors',
              action === a
                ? (EXIT_ACTIONS.has(a) ? 'bg-down/15 text-down border-down/40' : 'bg-up/15 text-up border-up/40')
                : 'bg-bg-elevated text-text-muted border-bg-border hover:text-text-secondary')}>
            {a}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div><label className={labelCls}>股票代码</label><input value={f.stock_code} onChange={set('stock_code')} placeholder="600519" className={inputCls} /></div>
        <div><label className={labelCls}>名称<span className="text-text-muted/60">（与代码填一个）</span></label><input value={f.stock_name} onChange={set('stock_name')} placeholder="贵州茅台" className={inputCls} /></div>
        <div><label className={labelCls}>交易时间 *</label><input type="datetime-local" value={f.trade_time} onChange={set('trade_time')} className={inputCls} /></div>
        <div><label className={labelCls}>价格 *</label><input value={f.price} onChange={set('price')} inputMode="decimal" placeholder="1420.5" className={inputCls} /></div>
        <div><label className={labelCls}>仓位 %</label><input value={f.position_pct} onChange={set('position_pct')} inputMode="decimal" placeholder="20" className={inputCls} /></div>
      </div>

      {/* 建仓专属：理由/止损/目标 */}
      {!isExit && (
        <div className="space-y-2">
          <div><label className={labelCls}>买入理由 *（事前摩擦）</label><textarea value={f.reason} onChange={set('reason')} rows={2} placeholder="为什么买？写不出就别买" className={cn(inputCls, 'resize-none')} /></div>
          <div className="grid grid-cols-2 gap-2">
            <div><label className={labelCls}>计划止损 *</label><input value={f.planned_stop} onChange={set('planned_stop')} inputMode="decimal" placeholder="1380" className={inputCls} /></div>
            <div><label className={labelCls}>目标价</label><input value={f.target} onChange={set('target')} inputMode="decimal" placeholder="1500" className={inputCls} /></div>
          </div>
        </div>
      )}

      {/* 平仓专属：触发/盈亏 */}
      {isExit && (
        <div className="space-y-2">
          <div>
            <label className={labelCls}>卖出触发</label>
            <div className="flex flex-wrap gap-1">
              {EXIT_REASONS.map(r => (
                <button key={r} onClick={() => setF(p => ({ ...p, exit_reason: p.exit_reason === r ? '' : r }))}
                  className={cn('text-[11px] px-2 py-1 rounded-lg border transition-colors',
                    f.exit_reason === r ? 'bg-warn/15 text-warn border-warn/40' : 'bg-bg-elevated text-text-muted border-bg-border')}>
                  {r}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div><label className={labelCls}>已实现盈亏 ¥</label><input value={f.realized_pnl} onChange={set('realized_pnl')} inputMode="decimal" placeholder="-8400" className={inputCls} /></div>
            <div><label className={labelCls}>盈亏 %</label><input value={f.pnl_pct} onChange={set('pnl_pct')} inputMode="decimal" placeholder="-4.3" className={inputCls} /></div>
          </div>
        </div>
      )}

      {/* 情绪标签 */}
      <div>
        <label className={labelCls}>情绪 / 行为标签（自评,越诚实越有用）</label>
        <div className="flex flex-wrap gap-1">
          {EMOTIONS.map(em => (
            <button key={em} onClick={() => setF(p => ({ ...p, emotion_tag: p.emotion_tag === em ? '' : em }))}
              className={cn('text-[11px] px-2 py-1 rounded-lg border transition-colors',
                f.emotion_tag === em ? EMOTION_STYLE[em] : 'bg-bg-elevated text-text-muted border-bg-border')}>
              {em}
            </button>
          ))}
        </div>
      </div>

      <div><label className={labelCls}>备注</label><input value={f.note} onChange={set('note')} placeholder="当时的想法/情绪…" className={inputCls} /></div>

      {err && (
        <div className="flex items-center gap-1.5 text-xs text-warn bg-warn/10 border border-warn/25 rounded-lg px-2.5 py-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />{err}
        </div>
      )}

      <button onClick={submit} disabled={create.isPending}
        className="w-full py-2 rounded-lg bg-accent/15 border border-accent/30 text-accent text-sm font-medium hover:bg-accent/25 transition-colors disabled:opacity-50">
        {create.isPending ? '记录中…' : '记录这一笔'}
      </button>
    </div>
  )
}

function SummaryCard({ label, value, color }: { label: string; value: React.ReactNode; color?: 'up' | 'down' }) {
  return (
    <div className="card px-4 py-2.5">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className={cn('text-lg font-bold font-mono leading-tight mt-0.5',
        color === 'up' ? 'text-up' : color === 'down' ? 'text-down' : 'text-text-primary')}>{value}</div>
    </div>
  )
}
