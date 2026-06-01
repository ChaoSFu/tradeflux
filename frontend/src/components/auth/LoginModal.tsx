import { useState } from 'react'
import { login } from '@/api/auth'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/utils/cn'
import { X, LogIn, Eye, EyeOff } from 'lucide-react'

interface LoginModalProps {
  onClose: () => void
}

export function LoginModal({ onClose }: LoginModalProps) {
  const doLogin = useAuthStore(s => s.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return
    setLoading(true)
    setError(null)
    try {
      const res = await login(username, password)
      doLogin(res.access_token, res.username)
      onClose()
    } catch (err: any) {
      setError(err.message ?? '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative bg-bg-card border border-bg-border rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6"
        onClick={e => e.stopPropagation()}
      >
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-text-muted hover:text-text-primary transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        {/* Header */}
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-text-primary">管理员登录</h2>
          <p className="text-xs text-text-muted mt-1">登录后可执行数据更新操作</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-text-secondary mb-1.5">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              className="w-full bg-bg-elevated border border-bg-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent/60 transition-colors"
              placeholder="admin"
            />
          </div>

          <div>
            <label className="block text-xs text-text-secondary mb-1.5">密码</label>
            <div className="relative">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full bg-bg-elevated border border-bg-border rounded-lg px-3 py-2 pr-9 text-sm text-text-primary focus:outline-none focus:border-accent/60 transition-colors"
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPwd(v => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
              >
                {showPwd ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          {error && (
            <p className="text-xs text-down bg-down/10 border border-down/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className={cn(
              'w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-colors',
              loading || !username || !password
                ? 'bg-bg-elevated text-text-muted cursor-not-allowed'
                : 'bg-accent text-white hover:bg-accent/90',
            )}
          >
            <LogIn className="w-3.5 h-3.5" />
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
