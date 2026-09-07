import { AlertTriangle, WifiOff } from 'lucide-react'
import { LoadingRows } from '@/components/common/LoadingSpinner'

/** useQuery 结果里这个组件用得到的部分。**结构类型，不依赖泛型参数** */
export interface QueryLike {
  isPending: boolean
  error: unknown
  /** 'fetching' | 'paused' | 'idle'。paused = react-query 认为网络不可用，重试挂起 */
  fetchStatus: 'fetching' | 'paused' | 'idle'
  /** 重试尚未耗尽时 error 还是 null，但这里已经有上一次失败的原因了 */
  failureReason?: unknown
  failureCount?: number
}

const msgOf = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * **Error ≠ Empty ≠ Unknown ≠ 卡住。**
 *
 * 接口挂了显示「涨停 0 · 跌停 0 · 无板块」，跟"今天真的一个涨停都没有"长得
 * 一模一样，而这两件事对看盘的人是完全相反的信息。
 *
 * 这个组件把状态摊成四种，每一种说的话都不同：
 *
 *   失败      重试耗尽，error 落地
 *   重试中    还在 pending，但 failureReason 已经有值——**不能只转圈**，
 *             转圈跟"正在加载"没区别，而这时其实已经失败过一次了
 *   已挂起    fetchStatus === 'paused'：react-query 判定离线，重试不会自己继续。
 *             这种情况下永远等不到 error，只给个骨架屏就是无限假装在加载
 *   空        确实拿到了数据，只是没有内容
 *
 * ## 为什么不能用 isLoading
 *
 * v5 里 `isLoading === isPending && isFetching`。**重试退避那几秒 isFetching 是
 * false 而 error 还没落地**，于是 isLoading 假、error 空、data 也空——用它判断
 * 就会掉进空态分支。
 *
 * 实测（2026-09-07，把 dev 代理指到死端口）：四个模块全部 500，页面显示
 * 「暂无数据」和「今天没有板块达到涨停雷达门槛，也没有跌停股」——**接口全挂**
 * 被渲染成了**今天没有行情**。这正是本页要防的事，而我第一版自己写了出来。
 *
 * 传整个 query 对象而不是几个布尔值，也是为了避免调用方在多个 query 之间手写
 * `&&` / `||`——第一版就写成了 `up.isLoading && down.isLoading`（两个都在加载才
 * 算加载）。
 */
export function QueryState({
  qs, isEmpty, emptyText = '暂无数据', rows = 3, children,
}: {
  /** 这一块依赖的所有 query。任何一个还没结果，整块就还不知道 */
  qs: QueryLike[]
  /** 只有全部 query 都拿到数据之后才判空 */
  isEmpty?: boolean
  emptyText?: string
  rows?: number
  children: React.ReactNode
}) {
  const failed = qs.find((q) => q.error)
  if (failed) {
    return (
      <div className="flex items-start gap-2 py-3 text-xs text-warn">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <div>
          <div className="font-medium">数据获取失败</div>
          <div className="text-[11px] text-text-muted mt-0.5 break-all">
            {msgOf(failed.error)}
          </div>
        </div>
      </div>
    )
  }

  const paused = qs.find((q) => q.isPending && q.fetchStatus === 'paused')
  if (paused) {
    return (
      <div className="flex items-start gap-2 py-3 text-xs text-warn">
        <WifiOff className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <div>
          <div className="font-medium">请求已挂起</div>
          <div className="text-[11px] text-text-muted mt-0.5 break-all">
            浏览器判定网络不可用，重试暂停，不会自动继续。
            {paused.failureReason ? ` 上次失败：${msgOf(paused.failureReason)}` : ''}
          </div>
        </div>
      </div>
    )
  }

  const retrying = qs.find((q) => q.isPending && q.failureReason)
  if (retrying) {
    return (
      <div className="flex items-start gap-2 py-3 text-xs text-text-secondary">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-warn/70" />
        <div>
          <div>重试中（第 {retrying.failureCount ?? 1} 次失败）</div>
          <div className="text-[11px] text-text-muted mt-0.5 break-all">
            {msgOf(retrying.failureReason)}
          </div>
        </div>
      </div>
    )
  }

  if (qs.some((q) => q.isPending)) return <LoadingRows rows={rows} />
  if (isEmpty) return <div className="py-3 text-xs text-text-muted">{emptyText}</div>
  return <>{children}</>
}

/**
 * 这个 query 现在算不算「取数出了问题」。
 *
 * **不能只看 `error`**：重试没耗尽时 error 还是 null，离线挂起时更是永远等不到
 * error。数据状态条要跟每一块的内容说同一件事，就得用同一个判据。
 */
export const queryFailed = (q: QueryLike) =>
  !!q.error || (q.isPending && (q.fetchStatus === 'paused' || !!q.failureReason))
