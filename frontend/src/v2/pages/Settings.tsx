/**
 * 设置 —— **全部复用 V1 已有能力，不复制第二套**。
 *
 *   数据更新   直接用 Header 里的 DataUpdateMenu（只加了 export，行为一行没改）
 *   板块配置   直接挂 V1 的 SectorConfig 组件
 *   股池配置   直接挂 V1 的 PoolConfig 组件
 *   账户       同一个 auth store
 *
 * 不新建 V2 的配置表、不复制业务逻辑。这些东西一旦有两份，两份就会分叉——
 * 这个仓库为「同一件事两套实现」栽过很多次。
 */
import { useState } from 'react'
import { DataUpdateMenu } from '@/components/layout/Header'
import { LoginModal } from '@/components/auth/LoginModal'
import SectorConfig from '@/pages/SectorConfig'
import PoolConfig from '@/pages/PoolConfig'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/utils/cn'

type Tab = 'update' | 'sector' | 'pool' | 'account'

export default function Settings() {
  const [tab, setTab] = useState<Tab>('update')
  const [showLogin, setShowLogin] = useState(false)
  const { isLoggedIn, username, logout } = useAuthStore()

  return (
    <div className="space-y-4">
      {showLogin && <LoginModal onClose={() => setShowLogin(false)} />}
      <div className="flex gap-2">
        {([['update', '数据更新'], ['sector', '板块配置'],
           ['pool', '股池配置'], ['account', '账户']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('text-xs px-3 py-1.5 rounded border transition-colors',
              tab === k ? 'border-accent/50 text-accent bg-accent/10'
                        : 'border-bg-border text-text-secondary hover:text-text-primary')}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'update' && (
        <section className="card p-4">
          <h2 className="text-sm text-text-primary">数据更新</h2>
          <p className="text-[11px] text-text-muted mt-1">
            跟 V1 顶栏是同一个组件、同一套任务，不是第二条更新链路。
          </p>
          <div className="mt-3">
            <DataUpdateMenu onRequestLogin={() => setShowLogin(true)} />
          </div>
        </section>
      )}
      {tab === 'sector' && <SectorConfig />}
      {tab === 'pool' && <PoolConfig />}
      {tab === 'account' && (
        <section className="card p-4">
          <h2 className="text-sm text-text-primary">账户</h2>
          {isLoggedIn ? (
            <div className="mt-2 text-xs text-text-secondary">
              已登录：<span className="text-text-primary">{username}</span>
              <button onClick={logout} className="ml-3 text-accent">登出</button>
            </div>
          ) : (
            <button onClick={() => setShowLogin(true)}
                    className="mt-2 text-xs text-accent">登录</button>
          )}
          <p className="text-[11px] text-text-muted mt-2">
            与 V1 共用同一个 auth store，登录状态互通。
          </p>
        </section>
      )}
    </div>
  )
}
