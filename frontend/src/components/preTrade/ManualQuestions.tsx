import { cn } from '@/utils/cn'
import type { JournalRow, ManualAnswers } from '@/api/preTradeCheck'

type YesNoKey = 'q1' | 'q2' | 'q3' | 'q4' | 'q5' | 'q6' | 'q7' | 'q8' | 'a_plus'

function YesNo({ value, onChange, bad }: {
  value: boolean | null; onChange: (v: boolean) => void; bad: boolean
}) {
  const btn = (v: boolean, label: string) => {
    const on = value === v
    const danger = on && v === bad
    return (
      <button type="button" onClick={() => onChange(v)}
              className={cn('rounded border px-2.5 py-0.5 text-xs transition-colors',
                on ? (danger ? 'border-danger/50 bg-danger/15 text-danger' : 'border-accent/50 bg-accent/15 text-accent')
                   : 'border-bg-border text-text-muted hover:text-text-secondary')}>
        {label}
      </button>
    )
  }
  return <div className="flex shrink-0 gap-1.5">{btn(true, '是')}{btn(false, '否')}</div>
}

const hm = (iso: string) => iso.slice(5, 16).replace('T', ' ')

/**
 * 一票否决的人工问题。**系统不猜你的心理**——8 题答「是」都直接 BLOCKED（失效条件在「你的计划」里写）；
 * 没回答不等于答了「否」，到不了 READY。
 */
export function ManualQuestions({ questions, answers, onChange, asOfBeforeEntry, earliest,
                                  recent, todayBuys, maxTrades }: {
  questions: { key: string; text: string }[]
  answers: ManualAnswers
  onChange: (a: ManualAnswers) => void
  asOfBeforeEntry: boolean
  earliest: string
  recent: JournalRow[]
  todayBuys: JournalRow[]
  maxTrades: number
}) {
  const set = (k: YesNoKey, v: boolean) => onChange({ ...answers, [k]: v })
  const textCls = 'w-full bg-bg-elevated border border-bg-border rounded-lg px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 resize-none'
  return (
    <div className="card p-4 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">我买它的主要原因是不是……</span>
        <span className="text-[11px] text-text-muted">答「是」就直接 BLOCKED</span>
      </div>
      <div className="divide-y divide-bg-border/40">
        {questions.map((q, i) => (
          <div key={q.key} className="flex items-center justify-between gap-3 py-1.5">
            <span className="text-sm text-text-secondary">{i + 1}. {q.text}</span>
            <YesNo value={answers[q.key as YesNoKey]} onChange={(v) => set(q.key as YesNoKey, v)}
                   bad />
          </div>
        ))}
        {asOfBeforeEntry && (
          <div className="flex items-center justify-between gap-3 py-1.5">
            <span className="text-sm text-warn">
              现在在 {earliest} 之前。是否有<b>事前写好</b>的 A+ 例外条件？
            </span>
            <YesNo value={answers.a_plus} onChange={(v) => set('a_plus', v)} bad={false} />
          </div>
        )}
      </div>

      {todayBuys.length >= maxTrades && (
        <p className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          今天已有 {todayBuys.length} 笔买入（{todayBuys.map((b) => `${hm(b.trade_time)} ${b.stock_name ?? b.stock_code}`).join('、')}），
          这一笔会直接 BLOCKED。
        </p>
      )}
      {todayBuys.length === 1 && (
        <label className="block space-y-1">
          <span className="text-xs text-warn">
            今天第 2 笔（第一笔：{hm(todayBuys[0].trade_time)} {todayBuys[0].stock_name ?? todayBuys[0].stock_code}）。
            第二笔门槛更高——这笔比第一笔多了什么证据？
          </span>
          <textarea rows={2} className={textCls} value={answers.second_trade_note}
                    onChange={(e) => onChange({ ...answers, second_trade_note: e.target.value })} />
        </label>
      )}
      {recent.length > 0 && (
        <label className="block space-y-1">
          <span className="text-xs text-warn">
            最近交易过这只票（{hm(recent[0].trade_time)} {recent[0].action} {recent[0].price}）。
            快速重入必须写出<b>新的市场事实</b>，不写会直接 BLOCKED：
          </span>
          <textarea rows={2} className={textCls} value={answers.new_market_fact}
                    placeholder="跟上次相比，市场 / 板块 / 个股发生了什么新的变化？"
                    onChange={(e) => onChange({ ...answers, new_market_fact: e.target.value })} />
        </label>
      )}
    </div>
  )
}
