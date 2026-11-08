import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Info, RotateCcw, Save } from 'lucide-react'
import { fetchLargeCapConfig, updateLargeCapConfig, type LargeCapConfig } from '@/api/largeCap'
import { Card } from '@/components/ui/card'
import { cn } from '@/utils/cn'

type Form = { threshold_yi: string; surge_amount_multiple: string; surge_volume_ratio: string; history_days: string; include_st: boolean }

const toForm = (c: LargeCapConfig): Form => ({
  threshold_yi: String(c.threshold_yi), surge_amount_multiple: String(c.surge_amount_multiple),
  surge_volume_ratio: String(c.surge_volume_ratio), history_days: String(c.history_days), include_st: c.include_st,
})

const INPUT = 'w-24 bg-bg-elevated border border-bg-border rounded px-2 py-1 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50 disabled:opacity-60'

/** 大市值概览的数值配置。全是确定的数值条件，不用自然语言 prompt。 */
export function LargeCapSettings({ editable }: { editable: boolean }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['large-cap-config'], queryFn: fetchLargeCapConfig })
  const [f, setF] = useState<Form | null>(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { if (data) setF(toForm(data)) }, [data])

  const mut = useMutation({
    mutationFn: updateLargeCapConfig,
    onSuccess: (resp) => {
      qc.setQueryData(['large-cap-config'], resp)
      qc.invalidateQueries({ queryKey: ['large-cap-overview'] })
      setF(toForm(resp))
      setMsg('已保存，页面立即按新设置重算（不会重新采集）')
      setTimeout(() => setMsg(''), 4000)
    },
    onError: (e: any) => setMsg(e?.message ?? '保存失败（需登录权限）'),  // eslint-disable-line @typescript-eslint/no-explicit-any
  })

  if (!data || !f) return null
  const dirty = JSON.stringify(f) !== JSON.stringify(toForm(data))
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: k === 'include_st' ? (e.target as HTMLInputElement).checked : e.target.value })

  return (
    <Card title="大市值监控设置" action={
      <span className={cn('text-xs px-1.5 py-0.5 rounded', data.custom_keys.length ? 'bg-accent/15 text-accent' : 'bg-bg-elevated text-text-muted')}>
        {data.custom_keys.length ? '自定义' : '默认'}
      </span>
    }>
      <p className="text-xs text-text-muted mb-3 leading-relaxed flex items-start gap-1">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        决定「大市值概览」的统计母体和放量口径。每天存的是全市场收盘快照，改门槛只是换个口径重算，
        不会重新采集，也不会把配置变化算成「今天有公司跨入 / 跨出」。
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2.5 text-sm">
        <label className="flex items-center gap-2">
          <span className="w-32 text-text-secondary">总市值门槛</span>
          <input className={INPUT} value={f.threshold_yi} onChange={set('threshold_yi')} disabled={!editable} inputMode="decimal" />
          <span className="text-text-muted text-xs">亿元（常用 {data.threshold_presets.join(' / ')}）</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="w-32 text-text-secondary">放量：成交额倍数</span>
          <input className={INPUT} value={f.surge_amount_multiple} onChange={set('surge_amount_multiple')} disabled={!editable} inputMode="decimal" />
          <span className="text-text-muted text-xs">× 前 20 日均额</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="w-32 text-text-secondary">放量：量比</span>
          <input className={INPUT} value={f.surge_volume_ratio} onChange={set('surge_volume_ratio')} disabled={!editable} inputMode="decimal" />
          <span className="text-text-muted text-xs">≥ 这个值也算放量</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="w-32 text-text-secondary">默认统计窗口</span>
          <select className={INPUT} value={f.history_days} onChange={set('history_days')} disabled={!editable}>
            {[5, 10, 20, 60].map((n) => <option key={n} value={n}>{n} 个交易日</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="w-32 text-text-secondary">包含 ST</span>
          <input type="checkbox" checked={f.include_st} onChange={set('include_st')} disabled={!editable} />
        </label>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className="w-32 text-text-secondary text-sm">北交所</span>不纳入（行情范围不含北交所）
        </div>
      </div>
      {editable && (
        <div className="flex items-center gap-3 mt-3">
          <button
            onClick={() => mut.mutate({
              threshold_yi: Number(f.threshold_yi), surge_amount_multiple: Number(f.surge_amount_multiple),
              surge_volume_ratio: Number(f.surge_volume_ratio),
              history_days: Number(f.history_days) as LargeCapConfig['history_days'], include_st: f.include_st,
            })}
            disabled={!dirty || mut.isPending}
            className={cn('flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
              dirty && !mut.isPending ? 'bg-accent text-white hover:bg-accent/90' : 'bg-bg-elevated text-text-muted cursor-not-allowed')}>
            <Save className="w-4 h-4" /> 保存
          </button>
          <button onClick={() => mut.mutate({ reset: true })} disabled={!data.custom_keys.length || mut.isPending}
                  className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary disabled:opacity-50">
            <RotateCcw className="w-3 h-3" /> 恢复默认
          </button>
          {msg && <span className="text-xs text-text-secondary">{msg}</span>}
        </div>
      )}
    </Card>
  )
}
