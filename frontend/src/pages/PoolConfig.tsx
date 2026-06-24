import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchPoolPrompts, updatePoolPrompts } from '@/api/admin'
import { Card } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { useAuthStore } from '@/store/auth'
import { RotateCcw, Save, Info, Lock } from 'lucide-react'

interface FieldProps {
  title: string
  hint: string
  value: string
  defaultValue: string
  custom: boolean
  editable: boolean
  onChange: (v: string) => void
  onReset: () => void
}

function PromptField({ title, hint, value, defaultValue, custom, editable, onChange, onReset }: FieldProps) {
  return (
    <Card title={title} action={
      <span className={cn('text-xs px-1.5 py-0.5 rounded', custom ? 'bg-accent/15 text-accent' : 'bg-bg-elevated text-text-muted')}>
        {custom ? '自定义' : '默认'}
      </span>
    }>
      <p className="text-xs text-text-muted mb-2 leading-relaxed flex items-start gap-1">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />{hint}
      </p>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
        readOnly={!editable}
        className={cn(
          'w-full bg-bg-elevated border border-bg-border rounded p-2.5 text-sm text-text-primary font-mono leading-relaxed resize-y focus:outline-none',
          editable ? 'focus:border-accent/50' : 'opacity-70 cursor-not-allowed',
        )}
      />
      <div className="flex items-center justify-between mt-2">
        {editable ? (
          <button
            onClick={onReset}
            className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
            title="恢复为代码内默认 prompt"
          >
            <RotateCcw className="w-3 h-3" /> 恢复默认
          </button>
        ) : <span />}
        {editable && value.trim() !== defaultValue.trim() && (
          <span className="text-xs text-warn">已修改，未保存</span>
        )}
      </div>
    </Card>
  )
}

export default function PoolConfig() {
  const qc = useQueryClient()
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const { data, isLoading } = useQuery({ queryKey: ['pool-prompts'], queryFn: fetchPoolPrompts })

  const [strong, setStrong] = useState('')
  const [limit, setLimit] = useState('')
  const [savedMsg, setSavedMsg] = useState('')

  useEffect(() => {
    if (data) { setStrong(data.strong_pool_keyword); setLimit(data.limit_move_keyword) }
  }, [data])

  const mut = useMutation({
    mutationFn: updatePoolPrompts,
    onSuccess: (resp) => {
      qc.setQueryData(['pool-prompts'], resp)
      setStrong(resp.strong_pool_keyword); setLimit(resp.limit_move_keyword)
      setSavedMsg('已保存，下次更新生效')
      setTimeout(() => setSavedMsg(''), 4000)
    },
  })

  if (isLoading || !data) return <LoadingSpinner />

  const dirty = strong.trim() !== data.strong_pool_keyword.trim() || limit.trim() !== data.limit_move_keyword.trim()

  return (
    <div className="space-y-4 animate-fade-in max-w-4xl">
      <div className="card p-4 border-l-4" style={{ borderLeftColor: '#4F9CF9' }}>
        <div className="text-sm text-text-secondary leading-relaxed">
          这两个 prompt 是调用<b className="text-text-primary">东方财富智能选股 API</b> 的筛选语句，决定<b className="text-text-primary">强势池</b>与<b className="text-text-primary">当日涨跌停池</b>纳入哪些股票。
          保存后，下一次<b className="text-text-primary">数据更新</b>（手动或盘后定时）即按新 prompt 调用接口，数据随之更新。语法用东财选股的自然语言条件，分号分隔、「或者/或」表 OR。
        </div>
      </div>

      {!isLoggedIn && (
        <div className="card p-3 flex items-center gap-2 text-sm text-warn border border-warn/30">
          <Lock className="w-4 h-4 shrink-0" /> 当前为只读查看，修改 prompt 需登录后操作。
        </div>
      )}

      <PromptField
        title="强势池 Prompt"
        hint="决定强势股池成员。例：主板非ST；非退市股；近60日涨停天数大于9 或 近20日涨幅前10。"
        value={strong}
        defaultValue={data.default_strong_pool_keyword}
        custom={data.is_strong_custom}
        editable={isLoggedIn}
        onChange={setStrong}
        onReset={() => setStrong(data.default_strong_pool_keyword)}
      />

      <PromptField
        title="当日涨跌停池 Prompt"
        hint="决定当日涨停/跌停池成员。例：非ST；非退市股票；涨停股票或者跌停股票。"
        value={limit}
        defaultValue={data.default_limit_move_keyword}
        custom={data.is_limit_custom}
        editable={isLoggedIn}
        onChange={setLimit}
        onReset={() => setLimit(data.default_limit_move_keyword)}
      />

      {isLoggedIn && (
      <div className="flex items-center gap-3">
        <button
          onClick={() => mut.mutate({ strong_pool_keyword: strong, limit_move_keyword: limit })}
          disabled={!dirty || mut.isPending}
          className={cn(
            'flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
            dirty && !mut.isPending ? 'bg-accent text-white hover:bg-accent/90' : 'bg-bg-elevated text-text-muted cursor-not-allowed',
          )}
        >
          <Save className="w-4 h-4" /> {mut.isPending ? '保存中…' : '保存'}
        </button>
        {savedMsg && <span className="text-sm text-up">{savedMsg}</span>}
        {mut.isError && <span className="text-sm text-down">保存失败（需登录权限）</span>}
      </div>
      )}
    </div>
  )
}
