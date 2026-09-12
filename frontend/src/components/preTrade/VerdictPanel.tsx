import { cn } from '@/utils/cn'
import type { CheckItem, Decision } from '@/api/preTradeCheck'
import { LevelIcon } from './levels'

const STYLE = {
  READY: 'border-accent/40 bg-accent/10 text-accent',
  WAIT: 'border-warn/40 bg-warn/10 text-warn',
  BLOCKED: 'border-danger/40 bg-danger/10 text-danger',
} as const
const LABEL = { READY: 'READY FOR MANUAL DECISION', WAIT: 'WAIT', BLOCKED: 'BLOCKED' } as const

function Group({ title, items, empty }: { title: string; items: CheckItem[]; empty: string }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-medium text-text-muted">{title}</div>
      {items.length === 0
        ? <div className="text-xs text-text-muted/60">{empty}</div>
        : items.map((i, n) => (
            <div key={`${i.module}-${i.key}-${n}`} className="flex gap-1.5 text-xs text-text-secondary">
              <LevelIcon level={i.level} />
              <span>{i.text}</span>
            </div>
          ))}
    </div>
  )
}

/**
 * 结论 + 三块证据。**没有分数**：结论只有 READY / WAIT / BLOCKED 三态，
 * 每一条都能在下面的模块里找到出处。
 */
export function VerdictPanel({ decision, asOf, mode, savedId }: {
  decision: Decision; asOf: string; mode: string; savedId: number | null
}) {
  const unmet = [...decision.vetoes, ...decision.unmet]
  return (
    <div className="card p-4 space-y-4">
      <div className={cn('rounded-lg border px-4 py-3', STYLE[decision.verdict])}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-lg font-bold tracking-wide"><span className="mr-2 text-xs font-normal opacity-80">总体</span>{LABEL[decision.verdict]}</span>
          <span className="text-[11px] opacity-80">
            {mode === 'LIVE' ? '实时' : '历史复盘'} · as_of {asOf.replace('T', ' ')} · {decision.rule_version}
            {savedId != null && ` · 已存档 #${savedId}`}
          </span>
        </div>
        <p className="mt-1.5 text-sm text-text-primary">{decision.summary}</p>
      </div>
      {(decision.dimensions?.length ?? 0) > 0 && (
        // 同样是 BLOCKED：机会不成熟？执行错了？仓位错了？三个维度分开看（v2）
        <div className="grid gap-2 md:grid-cols-3">
          {decision.dimensions!.map((d) => (
            <div key={d.key} className={cn('rounded-lg border px-3 py-2', STYLE[d.verdict])}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-semibold text-text-primary">{d.title}</span>
                <span className="text-xs font-bold">{d.verdict}</span>
              </div>
              <p className="mt-1 text-[11px] leading-snug text-text-secondary">{d.lead}</p>
            </div>
          ))}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Group title="做得好的地方" items={decision.positives} empty="—" />
        <Group title="需要警惕" items={decision.cautions} empty="没有" />
        <Group title="尚未满足 / 违反纪律" items={unmet} empty="没有" />
        <Group title="无法判断（数据拿不到）" items={decision.unknowns} empty="没有" />
      </div>
      <p className="text-[11px] text-text-muted">
        READY 只代表「市场、主线、个股、结构与风险没有发现冲突」，≠ 买入信号 ≠ 预测上涨。
        时间纪律通过和结构确认通过是两件事；单项拿不到数据只列出来，不算违规。
      </p>
    </div>
  )
}
