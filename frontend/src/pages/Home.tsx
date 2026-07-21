/**
 * 主页 — 平台介绍：功能导航 / 更新机制 / 联系方式
 */
import { Link } from 'react-router-dom'
import { cn } from '@/utils/cn'
import {
  Flame, LayoutDashboard, TrendingUp, ShieldAlert, Activity, Zap, BookOpen,
  RefreshCw, Mail, AlertTriangle, MousePointerClick, ArrowRight, Lock,
  Sunset, Sunrise, LineChart, NotebookPen, Telescope, ScanFace,
  Cpu, User, Bot, Gauge,
} from 'lucide-react'

// ─── 功能模块（每个模块一个主题色，用于图标与悬停光效）───────────────────────

const FEATURES = [
  { to: '/market-trend', icon: LineChart, name: '大盘趋势', color: '#5EA6FF', desc: '指数均线趋势 + 市场资金盘面' },
  { to: '/limit-moves', icon: Flame, name: '涨跌停概览', color: '#FF4560', desc: '每日涨跌停全景与集中板块' },
  { to: '/strong', icon: LayoutDashboard, name: '强势股概览', color: '#FFD700', desc: '强势股分组与多维评分' },
  { to: '/stocks', icon: TrendingUp, name: '活跃股池', color: '#5EA6FF', desc: '活跃候选股筛选与排序' },
  { to: '/watchlist', icon: ShieldAlert, name: '重点监控', color: '#F59E0B', desc: '严重异动名单与监管预警' },
  { to: '/sector-trend', icon: Activity, name: '趋势板块', color: '#00E5A0', desc: '多周期强势板块与生命周期' },
  { to: '/sector-emotion', icon: Activity, name: '情绪板块', color: '#B47CFF', desc: '板块资金情绪分布与迁移' },
  { to: '/signals', icon: Zap, name: '弱转强信号', color: '#FFB020', desc: '三类弱转强形态自动检测' },
  { to: '/review', icon: BookOpen, name: '日复盘', color: '#4E9CF5', desc: '每日点评与复盘记录' },
  { to: '/trade-journal', icon: NotebookPen, name: '交易复盘', color: '#B47CFF', desc: '记录操作，发现你反复犯的错' },
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

function PartyCard({ icon: Icon, color, name, tag, desc, emphasized }: {
  icon: React.ElementType; color: string; name: string; tag: string; desc: string; emphasized?: boolean
}) {
  return (
    <div
      className="card p-4 flex flex-col gap-2 border transition-colors"
      style={{ borderColor: emphasized ? `${color}66` : undefined, backgroundColor: emphasized ? `${color}0d` : undefined }}
    >
      <div className="flex items-center gap-2">
        <span className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ backgroundColor: `${color}1a`, color }}>
          <Icon className="w-4 h-4" />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text-primary leading-tight">{name}</div>
          <div className="text-[10px] font-mono mt-0.5" style={{ color }}>{tag}</div>
        </div>
      </div>
      <p className="text-xs text-text-muted leading-relaxed">{desc}</p>
    </div>
  )
}

function RuleCard({ n, title, desc }: { n: string; title: string; desc: string }) {
  return (
    <div className="card p-4 border border-bg-border">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-sm font-bold text-accent">{n}</span>
        <span className="text-sm font-semibold text-text-primary">{title}</span>
      </div>
      <p className="text-xs text-text-muted leading-relaxed mt-1.5">{desc}</p>
    </div>
  )
}

const MILESTONE_TONE: Record<string, string> = {
  done: 'text-down border-down/40 bg-down/10',
  next: 'text-warn border-warn/40 bg-warn/10',
  far: 'text-text-muted border-bg-border bg-bg-elevated',
}
const MILESTONE_RAIL: Record<string, string> = { done: '#26C281', next: '#F59E0B', far: '#737A96' }

function MilestoneCard({ status, tone, title, items }: {
  status: string; tone: 'done' | 'next' | 'far'; title: string; items: string[]
}) {
  return (
    <div className="card p-4 relative overflow-hidden border border-bg-border">
      <div className="absolute inset-y-0 left-0 w-[3px]" style={{ backgroundColor: MILESTONE_RAIL[tone] }} />
      <span className={cn('text-[10px] font-mono px-1.5 py-0.5 rounded border', MILESTONE_TONE[tone])}>{status}</span>
      <div className="text-sm font-semibold text-text-primary mt-2">{title}</div>
      <ul className="mt-2 space-y-1">
        {items.map((it) => (
          <li key={it} className="text-xs text-text-muted leading-relaxed pl-3 relative">
            <span className="absolute left-0 text-text-muted/60">▸</span>{it}
          </li>
        ))}
      </ul>
    </div>
  )
}

function AbilityCard({ icon: Icon, color, name, maps, desc }: {
  icon: React.ElementType; color: string; name: string; maps: string; desc: string
}) {
  return (
    <div className="card p-4 border border-bg-border flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ backgroundColor: `${color}1a`, color }}>
          <Icon className="w-4 h-4" />
        </span>
        <span className="text-sm font-semibold text-text-primary">{name}</span>
        <span className="text-[11px] font-mono" style={{ color }}>{maps}</span>
      </div>
      <p className="text-xs text-text-muted leading-relaxed">{desc}</p>
    </div>
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
          <p className="text-base text-text-secondary leading-relaxed mt-5 max-w-3xl">
            <span className="text-text-primary font-semibold">市场不奖励最聪明的人,奖励最能管住自己的人。</span>
            TradeFlux 让「客观的市场」照进「主观的你」,用 AI 居中做不带情绪的教练,把「知」一次次拉齐「行」——
            逼近知行合一,才是稳定盈利的路径。
          </p>
          <div className="flex items-center gap-2 mt-5 text-xs text-warn bg-warn/10 border border-warn/20 rounded-lg px-3 py-2 w-fit">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            本平台提供市场数据分析与个人行为复盘,不构成任何投资建议或买卖指令,交易决策请自行承担风险
          </div>
        </div>
      </div>

      {/* ── ① 系统定位：协调三方 ─────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '60ms' }}>
        <SectionTitle>系统定位 · 协调「市场 · 投资者 · AI」三方</SectionTitle>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <PartyCard icon={Activity} color="#5EA6FF" name="市场" tag="客观 · 不可预测"
            desc="一台把钱从「没纪律的人」转移到「有纪律的人」的机器。它只能顺应,不能预测。系统职责:用数据客观读懂它。" />
          <PartyCard icon={Cpu} color="#FFD700" name="系统 · AI" tag="桥梁 · 居中翻译" emphasized
            desc="站在市场与你之间,把市场的客观翻译给你、把你的主观照给你看,做一个不带情绪、只讲证据的教练。" />
          <PartyCard icon={User} color="#B47CFF" name="投资者" tag="主观 · 情绪驱动"
            desc="edge 与自我毁灭的共同来源。你的纪律是唯一可训练的优势。系统职责:照见你的弱点并持续纠偏。" />
        </div>
        <p className="text-xs text-text-muted px-1">
          系统的定位 = 让<span className="text-accent">客观的市场</span>与<span className="text-[#B47CFF]">主观的你</span>,在 <span className="text-dragon">AI</span> 的居中翻译下,不断逼近<span className="text-text-secondary">知行合一</span>。
        </p>
      </section>

      {/* ── ② 盈利之道：交易理论 ─────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '120ms' }}>
        <SectionTitle>盈利之道 · 交易的理论</SectionTitle>
        <p className="text-sm text-text-secondary leading-relaxed max-w-3xl">
          市场扣除成本后是<span className="text-text-primary">负和游戏</span>,平均的参与者必然亏钱。散户拼不过机构的信息与分析,唯一可长期依赖、且可训练的优势是<span className="text-text-primary font-semibold">纪律</span>——在别人做不到时,做到你本就知道该做的事。
        </p>
        {/* 盈利公式 */}
        <div className="card p-4 flex items-center gap-3 flex-wrap border border-bg-border">
          <span className="text-xs text-text-muted shrink-0">盈利期望</span>
          <span className="font-mono text-sm text-text-primary">
            盈利 = <span className="text-up">胜率 × 平均盈利</span> − <span className="text-down">败率 × 平均亏损</span> − <span className="text-warn">成本</span>
          </span>
        </div>
        {/* 四条结构性规则 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <RuleCard n="01" title="顺势" desc="不与趋势为敌。大势为守时,任何逆势买入都在降低胜率。" />
          <RuleCard n="02" title="截断亏损 · 让利润奔跑" desc="盈亏比是命门。快砍亏损、拿住盈利,才不会被处置效应反噬。" />
          <RuleCard n="03" title="固定风险仓位" desc="每笔只赌固定比例。越上头下得越重,是爆仓的唯一原因。" />
          <RuleCard n="04" title="低频 · 高确定性" desc="只做高确定性机会。交易越频繁,成本与情绪犯错越多。" />
        </div>
        <div className="rounded-lg border border-accent/25 bg-accent/5 px-4 py-2.5 text-sm text-text-secondary">
          <span className="text-accent font-semibold">大概率盈利 = 顺应规律 × 执行纪律</span>——四条全是「行为」,不是「预测」。你不需要预测未来,只需要不偏离你已经知道的对的事。
        </div>
      </section>

      {/* ── ③ 系统能力：如何帮你盈利 ─────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '180ms' }}>
        <SectionTitle>系统能力 · 如何帮你盈利</SectionTitle>
        <p className="text-sm text-text-muted max-w-3xl">系统的每一项能力,都对应盈利理论里的一环:</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <AbilityCard icon={Telescope} color="#5EA6FF" name="读市场" maps="→ 帮你顺势 · 判攻守"
            desc="大盘趋势(均线体系)、板块轮动退潮、涨跌停与一字板、情绪温度与建议仓位——给你一个不带情绪的市场坐标。" />
          <AbilityCard icon={ScanFace} color="#B47CFF" name="照自己" maps="→ 帮你守纪律"
            desc="交易复盘记录每一笔操作,检测引擎识别逆势加仓、向下摊平、报复性交易、满仓越线——把你的偏离量化出来。" />
          <AbilityCard icon={Bot} color="#FFB020" name="AI 教练" maps="→ 帮你缩小知行差 · 进化"
            desc="叙事化归因、事前良知反问、环境高危预警、按成熟度因材施教——用你自己的数据,做不带情绪的教练。" />
        </div>
        {/* 北极星指标 */}
        <div className="card p-4 flex items-start gap-3 border border-dragon/30 bg-dragon/5">
          <span className="w-9 h-9 rounded-lg bg-dragon/15 flex items-center justify-center shrink-0 text-dragon">
            <Gauge className="w-[18px] h-[18px]" />
          </span>
          <div>
            <div className="text-sm font-semibold text-text-primary">北极星 · 知行差指数</div>
            <p className="text-xs text-text-muted leading-relaxed mt-1">
              一个头条数字 = 你的操作偏离「客观市场环境」与「你自己定的规则」有多远。看着它逐月缩小,就是你的盈利能力在真实成长——两个引擎,最终缝成这一件事。
            </p>
          </div>
        </div>
      </section>

      {/* ── ④ 进化路线 ───────────────────────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '220ms' }}>
        <SectionTitle>进化路线 · 里程碑</SectionTitle>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <MilestoneCard status="已上线" tone="done" title="市场规律引擎 + 数据基建"
            items={['大盘趋势·涨跌停·板块·情绪温度', '每日自动更新 + 数据入库持久化']} />
          <MilestoneCard status="已上线" tone="done" title="交易复盘 P1 · 记录与镜子"
            items={['记录每笔操作 + 事前摩擦', '市场环境快照 · 账号隔离']} />
          <MilestoneCard status="下一步 · P2" tone="next" title="检测 · 画像 · AI 单笔复盘"
            items={['检测逆势加仓/摊平/报复/越线', '行为画像 · 我的软肋 · AI 归因']} />
          <MilestoneCard status="规划 · P3+" tone="far" title="重点跟踪 · 红线 · 进化"
            items={['环境高危预警 · 周期 AI 总结', '知行差指数 · 交易者成熟度模型']} />
        </div>
      </section>

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

      {/* ── 更新机制（精简为一行说明）───────────────────────────────────── */}
      <section className="space-y-3 animate-fade-in-up" style={{ animationDelay: '260ms' }}>
        <SectionTitle>数据更新</SectionTitle>
        <div className="card p-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-text-muted border border-bg-border">
          <span className="flex items-center gap-1.5"><Sunset className="w-3.5 h-3.5 text-accent/70" />盘后 15:30 自动</span>
          <span className="flex items-center gap-1.5"><Sunrise className="w-3.5 h-3.5 text-accent/70" />盘前 9:26–9:28 自动</span>
          <span className="flex items-center gap-1.5"><RefreshCw className="w-3.5 h-3.5 text-accent/70" />失败自动重试</span>
          <span className="flex items-center gap-1.5"><MousePointerClick className="w-3.5 h-3.5 text-accent/70" />手动更新
            <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-warn/10 text-warn border border-warn/30"><Lock className="w-2.5 h-2.5" />需登录</span>
          </span>
          <span className="w-full text-text-muted/70 leading-relaxed">
            数据来自东方财富等公开行情接口，口径为每日快照（非实时）；账号获取见下方联系方式。
          </span>
        </div>
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
