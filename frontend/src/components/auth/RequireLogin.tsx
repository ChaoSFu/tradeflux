import { Navigate, Outlet } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'

/**
 * 登录闸门 —— **挂在路由树上，不是包在每个页面外面**。
 *
 * 2026-10-11 起：除主页外，全部页面要登录才能看。
 *
 * 为什么是布局路由而不是 `<ProtectedRoute><Page /></ProtectedRoute>`：逐页包装
 * 有个必然的漏洞——**以后新加一条路由，忘了包就是公开的**，而这种遗漏不报错、
 * 不告警，界面上也完全看不出来，只有有人撞见才会发现。写成布局路由之后，新路由
 * 只要落在这个 `<Route>` 底下就自动受保护，**默认站在安全那一边**。
 *
 * 未登录一律跳回主页：登录入口在顶栏，而主页本身不需要登录，不会出现"跳到一个
 * 同样进不去的页面"的死循环。
 *
 * **这一层只挡界面。** 后端数据接口除了管理类（数据体检、触发更新、配置写入）
 * 之外目前仍是公开的，想挡住数据本身要在后端加 require_auth。
 */
export function RequireLogin() {
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  if (!isLoggedIn) return <Navigate to="/" replace />
  return <Outlet />
}
