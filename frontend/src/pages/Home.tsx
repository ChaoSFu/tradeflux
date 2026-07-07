/**
 * 主页 — 平台介绍：功能导航 / 数据来源 / 更新机制 / 联系方式
 */
import { Link } from 'react-router-dom'
import {
  Flame, LayoutDashboard, TrendingUp, ShieldAlert, Activity, Zap, BookOpen,
  Database, RefreshCw, Mail, AlertTriangle, Clock, MousePointerClick,
  CandlestickChart, Landmark, ListChecks, ArrowRight,
} from 'lucide-react'

// ─── 功能模块 ─────────────────────────────────────────────────────────────────

const FEATURES = [
  {
    to: '/limit-moves', icon: Flame, name: '涨跌停概览',
    desc: '每日涨停/跌停全景：走势曲线、主导板块、集中板块与二板梯队，支持叠加板块曲线对比、历史交易日回看',
  },
  {
    to: '/strong', icon: LayoutDashboard, name: '强势股概览',
    desc: '强势股池按板块分组，龙头分 / 情绪分 / 风险分多维评分排序，识别主线与龙头',
  },
  {
    to: '/stocks', icon: TrendingUp, name: '活跃股池',
    desc: '全部活跃候选股列表，支持板块搜索筛选、多字段排序与个股详情跳转',
  },
  {
    to: '/watchlist', icon: ShieldAlert, name: '重点监控',
    desc: '交易所严重异常波动名单（监管中 / 即将解除 / 近期解除）+ 即将进入监管的实时预测',
  },
  {
    to: '/sector-trend', icon: Activity, name: '趋势板块',
    desc: '5 / 10 / 20 日周期强势板块排行与生命周期阶段（启动→扩张→高潮→分歧→衰退）',
  },
  {
    to: '/sector-emotion', icon: Activity, name: '情绪板块',
    desc: '按情绪维度分组的板块视图，观察资金情绪在板块间的分布与迁移',
  },
  {
    to: '/signals', icon: Zap, name: '弱转强信号',
    desc: '炸板修复、分歧修复、反弹加速三类弱转强形态的自动检测',
  },
  {
    to: '/review', icon: BookOpen, name: '日复盘',
    desc: '每日市场点评与复盘记录，沉淀交易认知',
  },
]

// ─── 数据来源 ─────────────────────────────────────────────────────────────────

const DATA_SOURCES = [
  {
    icon: ListChecks, name: '东方财富 · 智能选股',
    desc: '涨跌停与强势股名单的权威来源（收盘后名单即当日全集），同时提供股票名称与 ST 状态，摘帽/改名自动修正',
  },
  {
    icon: CandlestickChart, name: '东方财富 · 历史K线',
    desc: '个股日K线与换手率，用于计算 60 日窗口指标（连板数、涨停天数、涨幅分位等）；腾讯财经作备用源',
  },
  {
    icon: Landmark, name: '东方财富 · 数据中心',
    desc: '交易所严重异常波动股票名单（监管口径）与严重异动预测接口，驱动「重点监控」模块',
  },
  {
    icon: Database, name: 'AkShare',
    desc: '全市场股票列表兜底（新上市股票补名称时使用），正常更新路径不依赖',
  },
]

// ─── 更新机制 ─────────────────────────────────────────────────────────────────

const UPDATE_ITEMS = [
  {
    icon: Clock, name: '盘后自动更新',
    desc: '交易日 15:30 后自动全量更新：抓取当日行情名单、重算窗口指标与评分、写入每日快照',
  },
  {
    icon: Clock, name: '盘前自动更新',
    desc: '交易日 9:26–9:28（集合竞价之后、开盘之前）自动刷新一次，修正隔夜口径',
  },
  {
    icon: RefreshCw, name: '失败自动重试',
    desc: '更新失败后 10 分钟自动重试，最多 3 次；两个定时任务互斥，不会并发重复更新',
  },
  {
    icon: MousePointerClick, name: '手动更新',
    desc: '顶栏「更新数据」按钮可随时手动触发更新，并实时查看任务进度',
  },
]

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function Home() {
  return (
    <div className="space-y-6 animate-fade-in max-w-5xl">

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <div className="card p-6 border border-accent/20 bg-gradient-to-br from-accent/10 via-transparent to-transparent">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-accent/20 border border-accent/30 flex items-center justify-center shrink-0">
            <Activity className="w-5 h-5 text-accent" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-text-primary leading-tight">TradeFlux · 短线晴雨表</h1>
            <p className="text-sm text-text-secondary mt-0.5">A 股短线复盘与市场结构分析平台</p>
          </div>
        </div>
        <p className="text-sm text-text-secondary leading-relaxed mt-4">
          每个交易日自动抓取全市场涨跌停、强势股与板块数据，量化市场情绪温度、赚钱效应与板块轮动，
          帮助短线交易者快速完成盘后复盘：今天谁在涨、主线在哪里、情绪处于什么阶段、明天该用什么仓位应对。
        </p>
        <div className="flex items-center gap-2 mt-4 text-xs text-warn bg-warn/10 border border-warn/20 rounded-lg px-3 py-2 w-fit">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          本平台仅提供市场数据聚合与结构化分析，不构成任何投资建议，交易决策请自行承担风险
        </div>
      </div>

      {/* ── 功能模块 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-text-primary">功能模块</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {FEATURES.map(({ to, icon: Icon, name, desc }) => (
            <Link
              key={to}
              to={to}
              className="card p-4 flex gap-3 group hover:border-accent/40 border border-transparent transition-colors"
            >
              <span className="w-8 h-8 rounded-lg bg-bg-elevated flex items-center justify-center shrink-0 text-text-secondary group-hover:text-accent transition-colors">
                <Icon className="w-4 h-4" />
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                  {name}
                  <ArrowRight className="w-3.5 h-3.5 text-text-muted/0 group-hover:text-accent transition-colors" />
                </div>
                <p className="text-xs text-text-muted leading-relaxed mt-1">{desc}</p>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* ── 数据来源 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-text-primary">数据来源</h2>
        <div className="card divide-y divide-bg-border/40">
          {DATA_SOURCES.map(({ icon: Icon, name, desc }) => (
            <div key={name} className="p-4 flex gap-3">
              <span className="w-8 h-8 rounded-lg bg-bg-elevated flex items-center justify-center shrink-0 text-text-secondary">
                <Icon className="w-4 h-4" />
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium text-text-primary">{name}</div>
                <p className="text-xs text-text-muted leading-relaxed mt-1">{desc}</p>
              </div>
            </div>
          ))}
          <p className="px-4 py-3 text-xs text-text-muted/80">
            以上均为公开行情接口，平台仅做数据聚合、指标计算与可视化，不生产行情数据；数据可能存在延迟或缺失，请以交易所披露为准。
          </p>
        </div>
      </section>

      {/* ── 更新机制 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-text-primary">数据更新机制</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {UPDATE_ITEMS.map(({ icon: Icon, name, desc }) => (
            <div key={name} className="card p-4 flex gap-3">
              <span className="w-8 h-8 rounded-lg bg-bg-elevated flex items-center justify-center shrink-0 text-text-secondary">
                <Icon className="w-4 h-4" />
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium text-text-primary">{name}</div>
                <p className="text-xs text-text-muted leading-relaxed mt-1">{desc}</p>
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-text-muted/80 px-1">
          数据口径为<span className="text-text-secondary">每日快照</span>（非实时行情）；顶栏可查看最近一次定时/手动更新时间。登录后可编辑选股 Prompt 与板块展示配置。
        </p>
      </section>

      {/* ── 联系方式 ─────────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-text-primary">联系方式</h2>
        <div className="card p-5 flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="flex items-center gap-3 flex-1">
            <span className="w-10 h-10 rounded-lg bg-accent/15 border border-accent/25 flex items-center justify-center shrink-0">
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
            className="shrink-0 text-sm px-4 py-2 rounded-lg bg-accent/15 border border-accent/30 text-accent hover:bg-accent/25 transition-colors text-center"
          >
            发送邮件
          </a>
        </div>
      </section>

      {/* ── Footer 声明 ──────────────────────────────────────────────────── */}
      <p className="text-xs text-text-muted/60 leading-relaxed pb-4">
        TradeFlux 是市场研究与认知辅助工具：不提供投资建议，不产生交易信号，不做自动交易，不保证任何收益。
        股市有风险，入市需谨慎。
      </p>
    </div>
  )
}
