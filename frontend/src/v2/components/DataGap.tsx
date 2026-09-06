/** 数据缺口的统一表达。**宁可空着并说明，也不伪造。** */
import { AlertTriangle } from 'lucide-react'

export function DataGap({ what, why, plan }: { what: string; why: string; plan?: string }) {
  return (
    <div className="card p-4 border border-warn/30">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-warn" />
        <div className="text-[12px] leading-relaxed">
          <div className="text-warn font-medium">{what}</div>
          <div className="text-text-secondary mt-1">{why}</div>
          {plan && <div className="text-text-muted mt-1">{plan}</div>}
        </div>
      </div>
    </div>
  )
}

/** 加载中 / 无数据的统一占位，不用 0 顶替 */
export function Empty({ text = '暂无数据' }: { text?: string }) {
  return <div className="card p-8 text-center text-text-muted text-sm">{text}</div>
}
