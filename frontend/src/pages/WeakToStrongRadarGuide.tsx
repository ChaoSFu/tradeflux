/**
 * 弱转强雷达 · 实现说明页。
 * 帮助用户理解页面背后的闸门/状态机/风险呈现逻辑，不是产品功能本身。
 *
 * 维护约定：弱转强雷达每新增/调整一项闸门逻辑、状态机规则、默认参数或后续规划，
 * 都应同步更新本页对应 section（尤其是 GATES / ROADMAP 两个数组），保持"这个页面
 * 讲的是当前代码实际在做什么"——跟 backend/docs/WEAK_TO_STRONG_RADAR.md 是同一份
 * 事实的两种呈现（面向开发者 vs 面向使用者），修改其一时另一份也要检查是否需要跟进。
 */
import { Link } from 'react-router-dom'
import {
  ArrowLeft, ArrowRight, Radar, Gauge, Layers, Crosshair, TrendingUp, Ruler,
  ShieldAlert, CheckCircle2, Circle, AlertTriangle,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/utils/cn'

// ─── 内容数据（未来功能更新时，优先改这里的数组）───────────────────────────────

const GATES = [
  {
    icon: TrendingUp,
    name: '大盘闸门',
    en: 'Market Gate',
    summary: '指数趋势 + 风险偏好，四色分级',
    body: '大盘趋势分（上证40%+深证35%+创业板25%加权，复用既有指数趋势引擎）与风险偏好分（涨跌家数比+涨跌停比+两融余额5日变化率）合成 GREEN/YELLOW/ORANGE/RED 四色。默认只有 RED 硬性拦截，其余仅提示不拦截——是否降低仓位留给使用者自行判断，本雷达不做仓位建议。全局算一次，同一次刷新里所有候选共用同一个市场状态。',
  },
  {
    icon: Layers,
    name: '板块闸门',
    en: 'Sector Gate',
    summary: '板块强度/动量 + 7 分类',
    body: '板块强度分看排名 tag（5/10/20日强、涨停数、连板高度排名），动量分看"今天比昨天变了多少"（涨停数/连板高度/情绪分/成交额环比）。7 分类里只放行 NEW_START/EXPANDING/MAIN_UPTREND/HEALTHY_DIVERGENCE，衰退和死亡阶段直接拦截。',
  },
  {
    icon: Crosshair,
    name: '龙头闸门',
    en: 'Leader Gate',
    summary: '同题材内排名，未决不放行',
    body: '同一题材（板块）内按 Core Leader Score 排名——分数由排名分、强势池百分位、连板/涨停历史、资金容量、分歧日抗跌能力、板块龙头加成六项合成。第一二名分差不够大时两者都标"龙头未决"，不强行指定；排名第三名开外直接拦截。',
  },
  {
    icon: Gauge,
    name: '结构确认',
    en: 'Setup Confirmation',
    summary: '回踩后突破低点，非猜测',
    body: '7 态状态机的核心一环：现价收复"昨收与5日线中更高者"进入修复阶段，之后回踩不破前低、再次突破回踩低点，才算真正的结构确认——不是看到上涨就直接给信号。',
  },
  {
    icon: Ruler,
    name: '空间闸门',
    en: 'Space Gate',
    summary: '涨停空间不足则降级等待',
    body: '结构确认完成的瞬间，如果距离涨停已经不到 2%（可配置），说明追进去性价比很差，信号降级为"等待"而不是拦截——空间会随价格逐分钟变化，候选本身没有失效，跟前四道"一旦不过就判死"的闸门性质不同。',
  },
] as const

const FEATURES = [
  { name: '大盘闸门条', desc: '四色圆点 + 趋势分/风险偏好分 + 数据日期，实时反映当前是否适合新增买入类信号。' },
  { name: '候选表（最多15行）', desc: '状态/股票/板块/龙头/现价/涨跌幅/MA5/涨停空间/Stress R/R/监管风险/触发拦截原因，按状态优先级排序，BLOCK 自动垫底。' },
  { name: '点击展开 Checklist', desc: '8 组闸门检查结果逐条展示，未实现的组诚实显示灰色"Phase 2"标签，不伪造 ✓。' },
  { name: '立即刷新', desc: '登录后可手动触发一次快速状态刷新（目标5-10秒），独立锁文件，不受全量数据更新阻塞。' },
]

const LIMITATIONS = [
  { title: '候选成交额校验用的是实时报价，不是真正的历史逐日数据', body: '补历史成交额需要改动K线重建管线，那条管线里还有个未解决的历史bug，改动风险大于收益。改用批量实时报价代替；数据缺失时不排除候选，避免误伤。' },
  { title: '竞价Gap无独立数据源', body: '用"当前报价相对昨收的涨跌幅"近似，09:26定时任务运行时基本准确，盘中较晚时段首次手动刷新会有偏差。' },
  { title: '板块动量分上线首日无历史基准', body: '固定给50分（中性），需要运行数天才能开始反映真实的环比变化。' },
]

const ROADMAP_DONE = [
  '候选发现 / 板块闸门 / 龙头闸门 / 7态状态机 / Checklist / 事件日志',
  '大盘闸门（Market Gate）',
  '空间闸门降级判断 + 三层止损 + Stress R/R',
  '候选池成交额条件本地二次校验',
]

const ROADMAP_SKIPPED = [
  { title: '监管风险 0-100 连续分', body: '复查原始规格发现明确禁止对监管风险给出精确数值型说法，只允许四档粗分类，做连续分本身就是编造假精度，判断后不做。' },
]

const ROADMAP_PLANNED = [
  { title: '日内获利盘估算', body: '需要标注为"估算"，不能假装是真实筹码成本。' },
  { title: 'T+1 供给风险', body: '次日抛压量化。' },
  { title: 'Leadership Impact', body: '龙头对板块内其他股票的带动性。' },
  { title: '回测框架', body: '历史信号胜率/盈亏比复盘。' },
]

// ─── 小组件 ─────────────────────────────────────────────────────────────────

function SectionHead({ num, title, sub }: { num: string; title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-xs text-text-muted/70">{num}</span>
        <h2 className="text-lg font-bold text-text-primary">{title}</h2>
      </div>
      {sub && <p className="text-xs text-text-muted mt-1">{sub}</p>}
    </div>
  )
}

function FormulaBlock({ children }: { children: React.ReactNode }) {
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed bg-bg-elevated border border-bg-border border-l-2 border-l-accent rounded px-4 py-3 text-text-secondary overflow-x-auto">
      {children}
    </pre>
  )
}

export default function WeakToStrongRadarGuide() {
  return (
    <div className="space-y-4 animate-fade-in max-w-4xl">
      <Link
        to="/weak-to-strong-radar"
        className="inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-accent transition-colors"
      >
        <ArrowLeft className="w-3.5 h-3.5" /> 返回弱转强雷达
      </Link>

      {/* Hero */}
      <div className="card p-5">
        <div className="flex items-center gap-2 text-xs font-mono text-accent mb-3">
          <span className="w-1.5 h-1.5 rounded-full bg-accent shadow-[0_0_0_3px_rgba(94,166,255,0.15)]" />
          TRADEFLUX · 短线晴雨表
        </div>
        <h1 className="text-2xl font-bold text-text-primary mb-3 flex items-center gap-2">
          <Radar className="w-6 h-6 text-accent" /> 弱转强雷达是怎么实现的
        </h1>
        <p className="text-sm text-text-secondary leading-relaxed max-w-2xl mb-4">
          一个把"板块转强 → 龙头确立 → 结构确认 → 空间充裕"这条完整决策链路跑一遍的独立页面。
          核心原则贯穿所有设计：<b className="text-text-primary">「弱转强成立」不等于「值得买入」</b>
          ——候选必须同时通过五道硬性闸门才会给出可执行信号，绝不把几个指标线性加权后直接吐出一个 BUY。
        </p>
        <div className="flex flex-wrap gap-2">
          <Badge variant="up" className="gap-1"><CheckCircle2 className="w-3 h-3" /> Phase 1 · 候选发现/板块闸门/龙头闸门/状态机</Badge>
          <Badge variant="up" className="gap-1"><CheckCircle2 className="w-3 h-3" /> Phase 2 · 大盘闸门/空间闸门/止损与风险回报比</Badge>
          <Badge variant="muted" className="gap-1"><Circle className="w-3 h-3" /> Phase 3 · 日内数据相关功能（规划中）</Badge>
        </div>
      </div>

      {/* 1. 五道闸门 */}
      <div className="card p-5">
        <SectionHead num="01" title="五道闸门" sub="候选进入雷达后，依次经过五道关卡；任何一道不通过，状态机就不会给出 BUYABLE。" />

        <div className="flex items-stretch gap-1 overflow-x-auto pb-2 mb-5">
          {GATES.map((g, i) => (
            <div key={g.en} className="flex items-stretch">
              <div className="min-w-[124px] max-w-[124px] bg-bg-elevated border border-bg-border rounded-lg p-3">
                <g.icon className="w-4 h-4 text-accent mb-1.5" />
                <div className="text-xs font-semibold text-text-primary">{g.name}</div>
                <div className="text-[11px] text-text-muted mt-1 leading-snug">{g.summary}</div>
              </div>
              {i < GATES.length - 1 && (
                <div className="flex items-center justify-center w-5 shrink-0 text-text-muted/50">
                  <ArrowRight className="w-3.5 h-3.5" />
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="space-y-4">
          {GATES.map((g) => (
            <div key={g.en} className="flex gap-3">
              <g.icon className="w-4 h-4 text-accent shrink-0 mt-0.5" />
              <div>
                <div className="text-sm font-semibold text-text-primary">
                  {g.name} <span className="text-text-muted font-normal font-mono text-xs">{g.en}</span>
                </div>
                <p className="text-xs text-text-secondary leading-relaxed mt-1">{g.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 2. 页面功能总览 */}
      <div className="card p-5">
        <SectionHead num="02" title="页面功能总览" sub="/weak-to-strong-radar，侧边栏「弱转强雷达」入口。" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {FEATURES.map((f) => (
            <div key={f.name} className="bg-bg-elevated border border-bg-border rounded-lg p-3.5">
              <div className="text-sm font-semibold text-text-primary mb-1">{f.name}</div>
              <div className="text-xs text-text-muted leading-relaxed">{f.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 3. 状态机 */}
      <div className="card p-5">
        <SectionHead num="03" title="状态机" sub="7 态，两个随时可能触发的侧出口。" />
        <div className="flex flex-wrap items-center gap-2 font-mono text-xs mb-2">
          {['WATCH', 'READY', 'REPAIRING', 'CONFIRMING'].map((s) => (
            <span key={s} className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded border border-bg-border bg-bg-elevated text-text-secondary">{s}</span>
              <ArrowRight className="w-3 h-3 text-text-muted/50" />
            </span>
          ))}
          <span className="px-2.5 py-1 rounded border border-up/30 bg-up-dim text-up font-semibold">BUYABLE</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-xs mb-4">
          <span className="text-text-muted">侧出口</span>
          <span className="px-2.5 py-1 rounded border border-bg-border bg-bg-elevated text-text-muted">WAIT</span>
          <span className="px-2.5 py-1 rounded border border-down/30 bg-down-dim text-down">BLOCK</span>
        </div>

        <FormulaBlock>{`# 主链路
WATCH   → [竞价Gap ≥ 3%，仅09:25后判定一次]         → READY
WATCH/READY → [现价 > max(昨收, MA5)]                → REPAIRING
REPAIRING   → [跌破修复关键位]                        → WAIT
            → [突破回踩低点]                          → CONFIRMING
CONFIRMING  → [跌破回踩低点]                          → WAIT
            → [涨停空间不足]                          → WAIT
            → [否则]                                  → BUYABLE

# BLOCK 优先级（从高到低，任意态触发）
数据过期 → 大盘闸门=RED → 板块分类不允许 → 龙头未决/非核心 → 监管风险过高 → 候选观察期已过`}</FormulaBlock>

        <p className="text-xs text-text-muted leading-relaxed mt-3">
          WARNING / EXIT（持仓监控态）不实现——本仓库没有持仓/成交跟踪能力，硬做就是编数据。
          真实 VWAP 也缺失，用"昨收与5日线中更高者"近似，页面和文档都如实标注这是替代方案。
        </p>
      </div>

      {/* 4. 止损与风险回报比 */}
      <div className="card p-5">
        <SectionHead num="04" title="止损与风险回报比" sub="压力情景下的保守估算，不是完整期望收益模型，也不构成止损/止盈建议。" />

        <div className="text-sm font-semibold text-text-primary mb-2">三层止损（由紧到松）</div>
        <FormulaBlock>{`技术止损  = 回踩低点（缺失时退回 5 日均线）
标准止损  = 技术止损 × (1 − 2%)          # 留缓冲，避免正常噪音刚好触发
压力止损  = 现价 × (1 − 跌停幅度%)        # 模拟买入后次日直接跌停开盘`}</FormulaBlock>
        <p className="text-xs text-text-muted leading-relaxed mb-4">
          压力止损存在的意义：A股 T+1，买入当天不能卖出，如果次日低开跌停，这是真实存在、没法靠盯盘规避的风险，必须提前算进去。
        </p>

        <div className="text-sm font-semibold text-text-primary mb-2">Stress R/R</div>
        <FormulaBlock>{`Stress R/R = 今日剩余涨停空间% ÷ 跌停幅度%`}</FormulaBlock>
        <p className="text-xs text-text-muted leading-relaxed">
          分母是同板块类型（主板9.9%/科创创业19.9%/北交所29.9%/ST 4.95%）的常数，不是概率加权预期。
          这个数字只回答一个问题——"如果明天真跌停，今天剩的上涨空间值不值得担这个风险"。
        </p>
      </div>

      {/* 5. 数据链路 */}
      <div className="card p-5">
        <SectionHead num="05" title="数据链路" sub="盘前发现候选，盘中快速刷新状态——两条互不阻塞的独立路径。" />

        <div className="text-sm font-semibold text-text-primary mb-1.5">盘前 · 候选池发现</div>
        <p className="text-xs text-text-secondary leading-relaxed mb-4">
          每日更新流程（收盘15:30/盘前09:27）里新增一步：跑两路候选 Prompt（复用东方财富智能选股接口），
          本地二次校验排名、均线、成交额这些容易被上游解析错的条件——不信任接口返回的股票代码就直接当作最终结果。
          命中的股票续期，连续多天没再命中的候选超过观察窗口后自动失活，不物理删除、保留历史。
        </p>

        <div className="text-sm font-semibold text-text-primary mb-1.5">盘中 · 快速刷新</div>
        <p className="text-xs text-text-secondary leading-relaxed mb-4">
          独立 API，09:26 定时任务 + 手动按钮触发，目标5-10秒完成：查活跃候选 → 按题材分组跑板块/龙头闸门 →
          批量拉一次实时报价 → 状态机判定 → 状态真的发生变化才追加写一条事件日志。
          自己的文件锁，跟每日全量更新的锁完全分开，两边互不阻塞。
        </p>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border/40 text-text-muted/70">
                <th className="text-left py-1.5 pr-4 font-medium">数据表</th>
                <th className="text-left py-1.5 font-medium">说明</th>
              </tr>
            </thead>
            <tbody className="text-text-secondary">
              <tr className="border-b border-bg-border/20">
                <td className="py-2 pr-4 font-mono text-text-primary whitespace-nowrap">weak_to_strong_candidates</td>
                <td className="py-2">当前态，一股一行，每次刷新原地更新——板块/龙头打分、状态机状态、止损位、Stress R/R 等全部字段</td>
              </tr>
              <tr>
                <td className="py-2 pr-4 font-mono text-text-primary whitespace-nowrap">weak_to_strong_events</td>
                <td className="py-2">追加写日志，只在状态真实变化时插入一条，记录变化前后状态与触发/拦截原因</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* 6. 已知局限 */}
      <div className="card p-5">
        <SectionHead num="06" title="已知局限" sub="如实记录，不在产品里假装不存在。" />
        <div className="space-y-2.5">
          {LIMITATIONS.map((l) => (
            <div key={l.title} className="flex items-start gap-2.5 bg-bg-elevated border border-bg-border rounded-lg p-3.5">
              <ShieldAlert className="w-3.5 h-3.5 text-warn shrink-0 mt-0.5" />
              <div>
                <div className="text-xs font-semibold text-text-primary">{l.title}</div>
                <div className="text-xs text-text-muted leading-relaxed mt-1">{l.body}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 7. 后续规划 */}
      <div className="card p-5">
        <SectionHead num="07" title="后续规划" sub="Phase 1、Phase 2 原计划项目已全部完成，只剩 Phase 3。" />

        <div className="mb-5">
          <div className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2">
            已完成 <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-up-dim text-up">Phase 1+2</span>
          </div>
          <ul className="space-y-1.5">
            {ROADMAP_DONE.map((item) => (
              <li key={item} className="flex items-start gap-2 text-xs text-text-secondary">
                <CheckCircle2 className="w-3.5 h-3.5 text-up shrink-0 mt-0.5" /> {item}
              </li>
            ))}
          </ul>
        </div>

        <div className="mb-5">
          <div className="text-sm font-semibold text-text-primary mb-2">主动不做</div>
          {ROADMAP_SKIPPED.map((item) => (
            <div key={item.title} className="text-xs text-text-secondary leading-relaxed">
              <b className="text-text-primary">{item.title}</b> —— {item.body}
            </div>
          ))}
        </div>

        <div>
          <div className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2">
            规划中 <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-bg-elevated border border-dashed border-bg-border text-text-muted">Phase 3 · 待定方向</span>
          </div>
          <ul className="space-y-1.5 mb-3">
            {ROADMAP_PLANNED.map((item) => (
              <li key={item.title} className="flex items-start gap-2 text-xs text-text-secondary">
                <ArrowRight className="w-3.5 h-3.5 text-text-muted/50 shrink-0 mt-0.5" />
                <span><b className="text-text-primary">{item.title}</b> —— {item.body}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-text-muted leading-relaxed">
            四项都依赖分钟级行情数据，本仓库目前没有该数据源和同步机制——这是一个新的架构决策
            （数据从哪来、同步频率、保留策略），需要先确定做哪一项、怎么建，而不是直接动手写代码。
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-text-muted/60 pt-2 pb-6">
        <span className="flex items-center gap-1.5"><AlertTriangle className="w-3 h-3" /> 本页仅说明实现逻辑，不构成任何投资建议</span>
        <Link to="/weak-to-strong-radar" className={cn('text-accent hover:underline flex items-center gap-1')}>
          返回雷达页面 <ArrowRight className="w-3 h-3" />
        </Link>
      </div>
    </div>
  )
}
