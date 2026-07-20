/**
 * 主页 — 平台介绍：功能导航 / 更新机制 / 联系方式
 */
import { Link } from 'react-router-dom'
import {
  Flame, LayoutDashboard, TrendingUp, ShieldAlert, Activity, Zap, BookOpen,
  RefreshCw, Mail, AlertTriangle, MousePointerClick, ArrowRight, Lock,
  Sunset, Sunrise, LineChart, NotebookPen, Telescope, ScanFace,
} from 'lucide-react'

// ─── 功能模块（每个模块一个主题色，用于图标与悬停光效）───────────────────────

const FEATURES = [
  {
    to: '/market-trend', icon: LineChart, name: '大盘趋势', color: '#5EA6FF',
    desc: '核心指数均线体系判定（多空排列/月线季线/斜率/乖离）+ 融资融券、涨跌统计、成交分析',
  },
  {
    to: '/limit-moves', icon: Flame, name: '涨跌停概览', color: '#FF4560',
    desc: '每日涨停/跌停全景：走势曲线、主导板块、集中板块与二板梯队，支持叠加板块曲线对比、历史交易日回看',
  },
  {
    to: '/strong', icon: LayoutDashboard, name: '强势股概览', color: '#FFD700',
    desc: '强势股池按板块分组，龙头分 / 情绪分 / 风险分多维评分排序，识别主线与龙头',
  },
  {
    to: '/stocks', icon: TrendingUp, name: '活跃股池', color: '#5EA6FF',
    desc: '全部活跃候选股列表，支持板块搜索筛选、多字段排序与个股详情跳转',
  },
  {
    to: '/watchlist', icon: ShieldAlert, name: '重点监控', color: '#F59E0B',
    desc: '交易所严重异常波动名单（监管中 / 即将解除 / 近期解除）+ 即将进入监管的实时预测',
  },
  {
    to: '/sector-trend', icon: Activity, name: '趋势板块', color: '#00E5A0',
    desc: '5 / 10 / 20 日周期强势板块排行与生命周期阶段（启动→扩张→高潮→分歧→衰退）',
  },
  {
    to: '/sector-emotion', icon: Activity, name: '情绪板块', color: '#B47CFF',
    desc: '按情绪维度分组的板块视图，观察资金情绪在板块间的分布与迁移',
  },
  {
    to: '/signals', icon: Zap, name: '弱转强信号', color: '#FFB020',
    desc: '炸板修复、分歧修复、反弹加速三类弱转强形态的自动检测',
  },
  {
    to: '/review', icon: BookOpen, name: '日复盘', color: '#4E9CF5',
    desc: '每日市场点评与复盘记录，沉淀交易认知',
  },
  {
    to: '/trade-journal', icon: NotebookPen, name: '交易复盘', color: '#B47CFF',
    desc: '记录你的每一笔操作，让系统发现并纠正你反复在犯的交易错误（个人私有，登录后使用）',
  },
]

// ─── 更新机制 ─────────────────────────────────────────────────────────────────

const UPDATE_ITEMS = [
  {
    icon: Sunset, name: '盘后自动更新',
    desc: '交易日 15:30 后自动全量更新：抓取当日行情名单、重算窗口指标与评分、写入每日快照',
  },
  {
    icon: Sunrise, name: '盘前自动更新',
    desc: '交易日 9:26–9:28（集合竞价之后、开盘之前）自动刷新一次，修正隔夜口径',
  },
  {
    icon: RefreshCw, name: '失败自动重试',
    desc: '更新失败后 10 分钟自动重试，最多 3 次；两个定时任务互斥，不会并发重复更新',
  },
  {
    icon: MousePointerClick, name: '手动更新', needLogin: true,
    desc: '顶栏「更新数据」按钮可手动触发更新并实时查看任务进度；手动更新为管理能力，登录后才能使用',
  },
]

// ─── 分区标题（渐变短杠 + 标题）──────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
      <span className="w-4 h-1 rounded-full bg-gradient-to-r from-accent to-accent/10" />
      {children}
    </h2>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function Home() {
  return (
    <div className="space-y-7">

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <div className="card relative overflow-hidden p-7 border border-accent/20 animate-fade-in-up">
        {/* 漂移光晕（纯装饰） */}
        <div className="absolute -top-24 -left-16 w-80 h-56 rounded-full bg-accent/15 blur-3xl animate-glow-drift pointer-events-none" />
        <div className="absolute -bottom-28 right-10 w-96 h-56 rounded-full bg-[#B47CFF]/10 blur-3xl pointer-events-none" />
        {/* 顶部渐变描边 */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/70 to-transparent" />

        <div className="relative">
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-xl bg-accent/15 border border-accent/30 flex items-center justify-center shrink-0 shadow-[0_0_24px_-6px_#5EA6FF]">
              <Activity className="w-5 h-5 text-accent animate-pulse-slow" />
            </div>
            <div>
              <h1 className="text-2xl font-bold leading-tight bg-gradient-to-r from-text-primary via-accent to-text-primary bg-clip-text text-transparent">
                TradeFlux · 短线晴雨表
              </h1>
              <p className="text-sm text-text-secondary mt-1">客观读懂市场,诚实看清自己——纠正习惯,走向稳定盈利</p>
            </div>
          </div>
          <p className="text-sm text-text-secondary leading-relaxed mt-5 max-w-3xl">
            多数人亏钱不是不懂市场,而是<span className="text-text-primary font-medium">做不到知行合一</span>。TradeFlux 把「知」和「行」放在一起对账:
            一边用数据客观总结指数、板块、个股的运行趋势与情绪冷暖,给你一个不带情绪的市场坐标;
            一边用你的真实交易数据,暴露被情绪驱动的人性弱点,做出客观评价、指导你纠正习惯。逼近知行合一,才是稳定盈利的路径。
          </p>

          {/* 两个引擎 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-5">
            <div className="rounded-lg border border-accent/25 bg-accent/5 p-3.5">
              <div className="flex items-center gap-2 text-sm font-semibold text-accent">
                <Telescope className="w-4 h-4" /> 读市场 · 客观规律引擎
              </div>
              <p className="text-xs text-text-muted leading-relaxed mt-1.5">
                指数均线趋势、板块轮动退潮、涨跌停与情绪温度、建议仓位——回答「现在什么环境、主线在哪、该攻还是该守」。
              </p>
            </div>
            <div className="rounded-lg border border-[#B47CFF]/30 bg-[#B47CFF]/6 p-3.5">
              <div className="flex items-center gap-2 text-sm font-semibold text-[#B47CFF]">
                <ScanFace className="w-4 h-4" /> 照自己 · 人性纠偏引擎
              </div>
              <p className="text-xs text-text-muted leading-relaxed mt-1.5">
                记录你的操作,发现逆势加仓、着急回本、不舍空仓等弱点,做出客观评价——回答「我又在重复哪个错、这次怎么管住自己」。
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 mt-5 text-xs text-warn bg-warn/10 border border-warn/20 rounded-lg px-3 py-2 w-fit">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            本平台提供市场数据分析与个人行为复盘,不构成任何投资建议或买卖指令,交易决策请自行承担风险
          </div>
        </div>
      </div>

      {/* ── 功能模块 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '80ms' }}>
        <SectionTitle>功能模块</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {FEATURES.map(({ to, icon: Icon, name, desc, color }, i) => (
            <Link
              key={to}
              to={to}
              style={{ '--fc': color, animationDelay: `${120 + i * 60}ms` } as React.CSSProperties}
              className="card relative overflow-hidden p-4 flex gap-3 group border border-bg-border animate-fade-in-up
                         transition-all duration-200 hover:-translate-y-1 hover:border-[var(--fc)]
                         hover:shadow-[0_8px_32px_-12px_var(--fc)]"
            >
              {/* 悬停时的角落色晕 */}
              <div
                className="absolute -top-10 -right-10 w-28 h-28 rounded-full blur-2xl opacity-0 group-hover:opacity-25 transition-opacity duration-300 pointer-events-none"
                style={{ backgroundColor: color }}
              />
              <span
                className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 transition-transform duration-200 group-hover:scale-110"
                style={{ backgroundColor: `${color}1a`, color }}
              >
                <Icon className="w-[18px] h-[18px]" />
              </span>
              <div className="min-w-0 relative">
                <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                  {name}
                  <ArrowRight
                    className="w-3.5 h-3.5 opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all duration-200"
                    style={{ color }}
                  />
                </div>
                <p className="text-xs text-text-muted leading-relaxed mt-1">{desc}</p>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* ── 更新机制 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '260ms' }}>
        <SectionTitle>数据更新机制</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {UPDATE_ITEMS.map(({ icon: Icon, name, desc, needLogin }, i) => (
            <div
              key={name}
              style={{ animationDelay: `${300 + i * 60}ms` }}
              className="card p-4 flex gap-3 border border-bg-border animate-fade-in-up
                         transition-all duration-200 hover:border-accent/40 hover:bg-bg-elevated/40"
            >
              <span className="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center shrink-0 text-accent/80">
                <Icon className="w-[18px] h-[18px]" />
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                  {name}
                  {needLogin && (
                    <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-warn/10 text-warn border border-warn/30">
                      <Lock className="w-2.5 h-2.5" />
                      需登录
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-muted leading-relaxed mt-1">{desc}</p>
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-text-muted/80 px-1">
          数据来自东方财富等公开行情接口，口径为<span className="text-text-secondary">每日快照</span>（非实时行情）；顶栏可查看最近一次定时/手动更新时间。
          <span className="text-text-secondary">手动更新为管理能力，登录后才能获取</span>；登录后还可编辑选股 Prompt 与板块展示配置。账号获取请见下方联系方式。
        </p>
      </section>

      {/* ── 联系方式 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '480ms' }}>
        <SectionTitle>联系方式</SectionTitle>
        <div className="card relative overflow-hidden p-5 flex flex-col sm:flex-row sm:items-center gap-4 border border-accent/15">
          <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-accent/50 to-transparent" />
          <div className="flex items-center gap-3 flex-1">
            <span className="w-11 h-11 rounded-xl bg-accent/15 border border-accent/25 flex items-center justify-center shrink-0 shadow-[0_0_20px_-6px_#5EA6FF]">
              <Mail className="w-5 h-5 text-accent" />
            </span>
            <div>
              <a
                href="mailto:chaofusjf@163.com"
                className="text-base font-mono font-semibold text-accent hover:underline"
              >
                chaofusjf@163.com
              </a>
              <p className="text-xs text-text-muted mt-0.5">
                问题沟通 · 权限获取 · 定制开发，欢迎邮件联系
              </p>
            </div>
          </div>
          <a
            href="mailto:chaofusjf@163.com?subject=TradeFlux%20%E5%92%A8%E8%AF%A2"
            className="shrink-0 text-sm px-4 py-2 rounded-lg bg-accent/15 border border-accent/30 text-accent text-center
                       transition-all duration-200 hover:bg-accent/25 hover:shadow-[0_0_24px_-8px_#5EA6FF] hover:-translate-y-0.5"
          >
            发送邮件
          </a>
        </div>
      </section>

      {/* ── Footer 声明 ──────────────────────────────────────────────────── */}
      <p className="text-xs text-text-muted/60 leading-relaxed pb-4 animate-fade-in-up" style={{ animationDelay: '560ms' }}>
        TradeFlux 是市场研究与认知辅助工具：不提供投资建议，不产生交易信号，不做自动交易，不保证任何收益。
        股市有风险，入市需谨慎。
      </p>
    </div>
  )
}
