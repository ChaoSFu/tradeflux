import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'
import type { AttributeType, ModuleMeta, Posture, ResponseStatus, Scenario, SectorTier } from '@/api/marketRecap'

export { NUM, fmtPct, pctCls, StockLink } from '@/components/largeCap/shared'

export const POSTURE_LABEL: Record<Posture, string> = {
  attack: '进攻', probe: '试错', defend: '防守', unclear: '暂不明确',
}
export const TIER_LABEL: Record<SectorTier, string> = { core: '核心主线', secondary: '次级观察', drop: '不再重点关注' }
export const ATTR_LABEL: Record<AttributeType, string> = {
  industry: '行业及产业链', concept: '共享概念', event: '事件催化',
  name: '名称联想', structure: '情绪结构', history: '历史市场记忆',
}
// 依据性质由属性类型决定（后端同一张表）：名称联想永远是 C
export const BASIS_OF: Record<AttributeType, 'A' | 'B' | 'C'> = {
  industry: 'A', event: 'A', concept: 'B', history: 'B', structure: 'B', name: 'C',
}
export const BASIS_LABEL = { A: 'A 实际产业 / 事件', B: 'B 共享概念 / 历史联动', C: 'C 名称 / 叙事联想' }
export const RESPONSE_LABEL: Record<ResponseStatus, string> = {
  leading: '领先', sync: '同步', lagging: '滞后', no_response: '未见明显响应', unknown: '无法判断', pending: '待验证',
}
export const GROUP_LABEL = { board: '连板', interval: '区间', lifecycle: '生命周期', turnover: '成交额' } as const

export const SCENARIO_META: Record<Scenario, { title: string; fields: [string, string][] }> = {
  A: { title: 'A 继续走强', fields: [
    ['trigger', '核心股票的触发条件'], ['attributes', '关注哪些属性方向'], ['sector_confirm', '关联板块需要什么确认'],
    ['watch_stocks', '哪些关联股需要确认'], ['weak_resonance', '哪些走势说明共振不足'], ['overheat', '什么情况说明已经过度上涨']] },
  B: { title: 'B 高位分歧', fields: [
    ['reseal', '是否换手修复 / 回封'], ['strong_peers', '哪些关联股保持强势'],
    ['successor', '是否有新核心接棒'], ['keep_watching', '什么条件支持继续观察']] },
  C: { title: 'C 明显转弱', fields: [
    ['logic_invalid', '哪些关联假设失效'], ['pause', '是否暂停相关方向'],
    ['drop_stocks', '哪些股票失去关注理由'], ['independent', '哪些股票有独立催化']] },
  N: { title: 'N 新核心出现', fields: [
    ['new_core', '新核心是谁'], ['same_mainline', '是否属于原主线'], ['regroup', '是否需要重组观察池'],
    ['evidence', '依据（高度 / 板块地位）'], ['verify_by', '后续用什么事实验证']] },
}

const STATUS_STYLE: Record<ModuleMeta['status'], [string, string]> = {
  ok: ['完整', 'bg-up/10 text-up'],
  partial: ['部分缺失', 'bg-warn/15 text-warn'],
  stale: ['非当日', 'bg-warn/15 text-warn'],
  missing: ['无数据', 'bg-bg-elevated text-text-muted'],
  error: ['请求失败', 'bg-down/15 text-down'],
}

export function MetaBadge({ meta }: { meta?: ModuleMeta }) {
  if (!meta) return null
  const [label, cls] = STATUS_STYLE[meta.status]
  return (
    <span className={cn('text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap', cls)}
          title={[meta.source, meta.trade_date && `数据日 ${meta.trade_date}`, meta.note].filter(Boolean).join('\n')}>
      {label}
    </span>
  )
}

/** 一个复盘模块。`kind` 区分系统事实和人工判断，视觉上分开 */
export function Section({ id, step, title, right, children }: {
  id: string
  step: string
  title: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <section id={id} className="card p-0 scroll-mt-16">
      <div className="px-4 py-2.5 border-b border-bg-border flex items-center gap-2 flex-wrap">
        <span className="text-[11px] text-accent font-mono">{step}</span>
        <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
        <div className="ml-auto flex items-center gap-2 flex-wrap">{right}</div>
      </div>
      <div className="p-4 space-y-4">{children}</div>
    </section>
  )
}

/** 人工输入区：左边一道紫线，跟系统事实一眼分开 */
export function Manual({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="border-l-2 border-purple-400/60 pl-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] px-1 rounded bg-purple-400/15 text-purple-300">人工</span>
        <span className="text-xs font-medium text-text-primary">{title}</span>
        {hint && <span className="text-[11px] text-text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

export function Facts({ title, meta, children }: { title: string; meta?: ModuleMeta; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] px-1 rounded bg-bg-elevated text-text-muted">数据</span>
        <span className="text-xs font-medium text-text-secondary">{title}</span>
        <MetaBadge meta={meta} />
        {meta?.note && meta.status !== 'ok' && (
          <span className="text-[11px] text-text-muted truncate max-w-[40rem]" title={meta.note}>{meta.note}</span>
        )}
      </div>
      {children}
    </div>
  )
}

export const INPUT = 'w-full bg-bg-elevated border border-bg-border rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-muted/60 focus:outline-none focus:border-accent/50 disabled:opacity-60'

export function TextArea({ value, onChange, placeholder, rows = 2, disabled }: {
  value: string | null | undefined
  onChange: (v: string) => void
  placeholder?: string
  rows?: number
  disabled?: boolean
}) {
  return (
    <textarea value={value ?? ''} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
              rows={rows} disabled={disabled} className={cn(INPUT, 'resize-y leading-relaxed')} />
  )
}

export const fmtYi = (yuan: number | null | undefined) =>
  yuan == null ? '—' : yuan >= 1e12 ? `${(yuan / 1e12).toFixed(2)}万亿` : `${(yuan / 1e8).toFixed(0)}亿`
export const fmtRatio = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`)
export const DIR_LABEL = { up: '↑', down: '↓', flat: '→' } as const

// 板块状态（跟 sector_mainline_service.STATE_LABELS 一致）
export const SECTOR_STATE_ZH: Record<string, string> = {
  ACCELERATION: '加速', MAIN_RISE: '主升', IGNITION: '点火', CLIMAX: '高潮',
  DIVERGENCE: '分歧', WEAKENING: '转弱', NONE: '无', UNKNOWN: '未知',
}
