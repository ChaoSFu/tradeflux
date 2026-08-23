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
    body: '大盘趋势分（上证40%+深证35%+创业板25%加权，复用既有指数趋势引擎）与风险偏好分（涨跌家数比+涨跌停比+T-1冻结群体次日真实反馈）合成 GREEN/YELLOW/ORANGE/RED 四色。风险偏好分里的"冻结群体反馈"同时以独立字段单独展示（赚钱效应/亏钱效应/负反馈分级），不再只是被揉进一个复合分数就看不出原因。默认只有 RED 硬性拦截，其余仅提示不拦截——是否降低仓位留给使用者自行判断，本雷达不做仓位建议。全局算一次，同一次刷新里所有候选共用同一个市场状态。',
  },
  {
    icon: Layers,
    name: '板块闸门',
    en: 'Sector Gate',
    summary: '板块强度/动量 + 7 分类',
    body: '板块强度分看排名 tag（5/10/20日强、涨停数、连板高度排名），动量分看"今天比昨天变了多少"（涨停数/连板高度/情绪分/成交额环比）。7 分类里 NEW_START/EXPANDING/MAIN_UPTREND/HEALTHY_DIVERGENCE 允许候选进入追踪，衰退和死亡阶段直接拦截；分歧阶段（HEALTHY_DIVERGENCE/HIGH_LEVEL_WARNING）背后的健康度原始分也单独展示，不只是一个二分类标签。',
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
    summary: 'H1/L1 两段式回踩，非任意反弹',
    body: '现价收复"昨收与VWAP/5日线中更高者"进入修复阶段，形成第一次修复高点(H1)；出现有效回踩（跌幅超过噪音阈值）后冻结H1、记录回踩低点(L1)；只有重新突破冻结的H1才算真正的结构确认——不是任意一次小反弹就给信号（2026-08-22 由弱定义修正为这个两段式判断）。',
  },
  {
    icon: Ruler,
    name: '空间闸门',
    en: 'Space Gate',
    summary: '涨停空间不足则降级等待',
    body: '结构确认完成的瞬间，如果距离涨停已经不到 2%（可配置），说明追进去性价比很差，信号降级为"等待"而不是拦截——空间会随价格逐分钟变化，候选本身没有失效，跟前几道"一旦不过就判死"的闸门性质不同。',
  },
  {
    icon: Crosshair,
    name: '主升板块封顶',
    en: 'Mainline Sector Cap',
    summary: '只有最强的少数板块才放行 BUYABLE',
    body: '结构确认(CONFIRMED)后，只有所属板块在当前 MAIN_UPTREND 强度前3名（可配置）以内才放行到 BUYABLE，其余候选照常追踪、结构照常推进，只是展示封顶在 CONFIRMING（2026-08-23新增）。"能做主升的板块不会很多"——收窄到只看逻辑最硬的少数主线，是主动收窄候选而不是提高发现速度。',
  },
] as const

const FEATURES = [
  { name: '大盘闸门条', desc: '四色圆点 + 趋势分/风险偏好分 + 数据日期，实时反映当前是否适合新增买入类信号。' },
  { name: '候选表（最多15行）', desc: '状态/股票/板块/龙头/现价/涨跌幅/MA5/涨停空间/Stress R/R/监管风险/触发拦截原因，按状态优先级排序，BLOCK 自动垫底。' },
  { name: '点击展开 Checklist', desc: '8 组闸门检查结果逐条展示，未实现的组诚实显示灰色"Phase 2"标签，不伪造 ✓。' },
  { name: '刷新数据并重新评估', desc: '登录后可手动触发一次快速状态刷新（目标5-10秒），独立锁文件，不受全量数据更新阻塞；30秒防连点冷却，统计栏实时显示距上次刷新多久，避免无意义重复请求。' },
]

const LIMITATIONS = [
  { title: '候选成交额条件不做本地二次校验', body: '完全信任东财Prompt自身的数值过滤。曾经短暂改用实时报价本地校验又主动撤回——那个值代表"当前累计成交额"，跟"昨日全天成交额"是两个不同变量，不是近似而是语义错误，尤其盘前运行时会系统性误伤候选。' },
  { title: '回踩结构识别基于离散采样，不是连续监控', body: 'H1/L1 判断依赖每次刷新拿到的价格快照，采样越稀疏越可能错过真正的高点/低点。目前只有09:26一次自动刷新，其余时段依赖手动点刷新——这是刻意的产品定位（2026-08-23明确），不追求发现速度，判断正确性优先，不会为了提升采样密度新增高频自动轮询。' },
  { title: '板块动量分无历史基准时不参与排序', body: '返回"数据积累中"而不是一个看起来真实的50分，需要运行数天才能开始反映真实的环比变化。' },
  { title: 'Core Leader Score 的板块龙头加成目前恒为0', body: '打分公式里的"板块龙头加成"依赖一个标记某只股票是"板块历史/公认龙头"的字段，但生产代码里从没有任何流程会真正写入这个标记（只有造假数据脚本会写），做一个可信的识别写入器是独立的新增工作，本次不做，如实记录在此。' },
]

const ROADMAP_DONE = [
  '候选发现 / 板块闸门 / 龙头闸门 / 状态机 / Checklist / 事件日志',
  '大盘闸门（Market Gate，风险偏好改用市场效应T-1反馈）',
  '空间闸门降级判断 + 三层止损 + Stress R/R',
  '2026-08-22 状态机纠错：H1/L1回踩结构、BLOCK不再死态、跨日重置、真实VWAP/竞价Gap、龙头未决与NEW_START软上限',
  '2026-08-23 结构进度/交易决策彻底拆成独立词表 + 数据合理性校验 + 市场板块负反馈独立字段 + H1/L1原始快照留存',
  '2026-08-23 候选专属日内快照引擎 + Golden Case 测试场景 + Prompt Parser Monitor',
  '2026-08-23 产品定位校准（手动触发为主）+ 主升板块封顶 + 修复数据过期后展示态不刷新的bug',
]

const ROADMAP_SKIPPED = [
  { title: '监管风险 0-100 连续分', body: '复查原始规格发现明确禁止对监管风险给出精确数值型说法，只允许四档粗分类，做连续分本身就是编造假精度，判断后不做。' },
  { title: '高频（30/60秒）自动候选快照轮询', body: '曾经评估过按结构态分级的自适应轮询方案，结合实际交易方式（只关注少量核心主线板块和龙头、买入条件严格）后撤回——判断正确性优先于发现速度，新增高频轮询会实质性提高对本就不稳定的东财接口的常态请求压力，判断后不做，只保留09:26一次盘前自动刷新+手动触发。' },
  { title: '"人工结构确认"字段', body: '曾经考虑过用人工勾选补偿离散采样可能错过的盘中细节，用户明确这类判断应该完全在系统之外由人工完成，不需要系统内的流程或字段——结构事实层"只看客观价格，不掺主观判断"这条不变式保持不变。' },
]

const ROADMAP_PLANNED = [
  { title: '回测框架', body: '历史数据批量验证策略有没有统计优势，是Engineering/语义/策略三层验证体系的最后一层。' },
  { title: 'T+1 供给风险', body: '次日抛压量化。' },
  { title: 'Leadership Impact', body: '龙头对板块内其他股票的带动性。' },
  { title: '日内获利盘估算', body: '需要标注为"估算"，不能假装是真实筹码成本，放最后做。' },
  { title: 'Mainline Clarity 主线分类器', body: '今天全市场有没有清晰的主线板块，还是多个板块在抢筹——是不同于大盘闸门的新分析维度。' },
  { title: 'Emotion Leader / Trend Anchor 角色分离展示', body: '情绪龙头和趋势/容量龙头是两种不同角色，跟弱转强分型工作有重叠，建议放在那之后一起做。' },
  { title: '多主题归属候选建模', body: '一只股票同时属于多个热门题材时，目前只按主板块打分，看不到在其他题材里的排名。' },
]

const ROADMAP_SETUP_TYPE = [
  '架构预留：weak_to_strong_candidates 新增 setup_type 字段（恒为 GENERIC）',
  'TREND / EMOTION / ANTI_NUCLEAR 三套 Policy（Shadow Mode 并行计算记录，不参与正式 BUYABLE 判断）',
  '积累 Golden Case（比如典型的深水反核案例）+ 回测验证后，再逐步切换生产决策',
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
    <div className="space-y-4 animate-fade-in">
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
          ——BUYABLE 是硬性拦截、软上限、结构本身走到确认这三层共同决定的结果，绝不把几个指标
          线性加权后直接吐出一个 BUY。
        </p>
        <div className="flex flex-wrap gap-2">
          <Badge variant="up" className="gap-1"><CheckCircle2 className="w-3 h-3" /> Phase 1 · 候选发现/板块闸门/龙头闸门/状态机</Badge>
          <Badge variant="up" className="gap-1"><CheckCircle2 className="w-3 h-3" /> Phase 2 · 大盘闸门/空间闸门/止损与风险回报比</Badge>
          <Badge variant="muted" className="gap-1"><Circle className="w-3 h-3" /> Phase 3 · 日内数据相关功能（规划中）</Badge>
        </div>
        <div className="flex items-start gap-2 mt-4 pt-4 border-t border-bg-border text-xs text-text-muted leading-relaxed">
          <AlertTriangle className="w-3.5 h-3.5 text-warn shrink-0 mt-0.5" />
          <span>
            本页和代码里出现的"单测全绿"只证明<b className="text-text-secondary">代码按设计的逻辑正确运行</b>
            （Engineering Validation），<b className="text-text-secondary">不代表这套弱转强定义本身能在实盘赚钱</b>
            （Strategy Validation）——后者需要历史回测（见"后续规划"），在那之前不应该把"代码没bug"
            误读成"策略有效"。
          </span>
        </div>
      </div>

      {/* 1. 六道关卡 */}
      <div className="card p-5">
        <SectionHead num="01" title="六道关卡" sub="候选进入雷达后依次经过六道关卡，但它们不是同一种性质——大盘/板块/龙头(非核心)命中即硬性拦截（展示BLOCK）；龙头(未决)/空间不足/主升板块封顶是软上限（压低能展示到的最高状态，不直接判死）；结构确认本身是价格驱动的进度条，不是一道会拦截谁的关卡，只是没走到那一步就自然还没到 BUYABLE。" />

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

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-4">
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
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
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

        <FormulaBlock>{`# 结构进度（只看价格走势，不受下面任何闸门/软上限影响，持续推进）
起点        → [现价 > max(昨收, VWAP或MA5)]            → 进入"修复"（开始记录修复高点H1）
修复中      → [跌破修复关键位]                        → 回到起点（结构失效，清空追踪）
            → [现价创新高]                            → 修复中（H1上移）
            → [回落超过噪音阈值，形成有效回踩]          → "回踩确认中"（冻结H1，记录回踩低点L1）
回踩确认中  → [跌破修复关键位]                        → 回到起点（回踩确认失败）
            → [突破冻结的H1]                          → "结构已确认"（二次突破，进度走完）
结构已确认  → [跌破修复关键位]                        → 回到起点（结构失效）

# 展示态 = 结构进度 + 硬性拦截 + 软上限，三者共同决定
硬性拦截命中（数据过期/大盘RED/板块不允许/龙头非核心/监管过高）→ 展示BLOCK，
                                                        底层结构进度照常推进，拦截解除展示立刻反映真实进度
软上限·板块刚起步(NEW_START) → 结构进度再高也只展示到 READY
软上限·龙头未决(undetermined) → 结构进度再高也只展示到 CONFIRMING
软上限·涨停空间不足 → 结构已确认时展示降级WAIT，底层进度不清空
只有结构进度走到"已确认"，且没被硬性拦截/软上限压低，展示态才会是 BUYABLE`}</FormulaBlock>

        <p className="text-xs text-text-muted leading-relaxed mt-3">
          WARNING / EXIT（持仓监控态）不实现——本仓库没有持仓/成交跟踪能力，硬做就是编数据。
          真实 VWAP 用实时报价的成交额/成交量算出，且必须落在当日最高最低价区间内才采信，
          量为0或数值不合理时才退回5日线；竞价Gap同样有涨跌停幅度合理性校验，异常值不会
          带偏结构判断。2026-08-22 之前 BLOCK 曾经是"死态"（闸门一旦不过就永远卡住，即使后续
          恢复也跳不出来）、回踩确认曾经是"任意小反弹即触发"的弱定义——都已修复；2026-08-23
          进一步把上面这两层在代码内部也彻底拆开成独立词表，避免结构层再被误写回交易决策，
          详见展开的 Checklist 里"结构事实已到X，被闸门/软上限临时覆盖展示"这类透明说明。
        </p>
      </div>

      {/* 4. 止损与风险回报比 */}
      <div className="card p-5">
        <SectionHead num="04" title="止损与风险回报比" sub="压力情景下的保守估算，不是完整期望收益模型，也不构成止损/止盈建议。" />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6">
          <div>
            <div className="text-sm font-semibold text-text-primary mb-2">三层止损（由紧到松）</div>
            <FormulaBlock>{`结构失效位（技术止损）= 回踩低点（缺失时退回 5 日均线）        # 价格跌破这里说明回踩修复的结构判断本身就错了
缓冲失效位（标准止损）= 结构失效位 × (1 − 2%)                 # 留缓冲，避免正常噪音刚好触发
T+1压力情景止损（压力止损）= 现价 × (1 − 跌停幅度%)            # 模拟买入后次日直接跌停开盘的最坏情况`}</FormulaBlock>
            <p className="text-xs text-text-muted leading-relaxed">
              "标准止损"这个名字容易被理解成行业公认做法，实际只是"结构失效位基础上留个缓冲"；
              T+1压力情景止损存在的意义：A股 T+1，买入当天不能卖出，如果次日低开跌停，这是真实
              存在、没法靠盯盘规避的风险，必须提前算进去，不是在预测明天真的会跌停。
            </p>
          </div>

          <div>
            <div className="text-sm font-semibold text-text-primary mb-2">Stress R/R</div>
            <FormulaBlock>{`Stress R/R = 今日剩余涨停空间% ÷ 跌停幅度%`}</FormulaBlock>
            <p className="text-xs text-text-muted leading-relaxed">
              分母用现有的动态涨跌停幅度计算（主板/创业板科创板/北交所/ST 各不相同，按证券规则
              算出，代码里从不是写死的常量），是同板块类型的固定值，不是概率加权预期。这个数字
              只回答一个问题——"如果最坏情况发生，今天剩的上涨空间值不值得担这个风险"。
            </p>
          </div>
        </div>
      </div>

      {/* 5. 数据链路 */}
      <div className="card p-5">
        <SectionHead num="05" title="数据链路" sub="盘前发现候选，盘中快速刷新状态——两条互不阻塞的独立路径。" />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 mb-4">
          <div>
            <div className="text-sm font-semibold text-text-primary mb-1.5">盘前 · 候选池发现</div>
            <p className="text-xs text-text-secondary leading-relaxed">
              每日更新流程（收盘15:30/盘前09:27）里新增一步：跑两路候选 Prompt（复用东方财富智能选股接口），
              本地二次校验排名、均线这些容易被上游解析错的条件——不信任接口返回的股票代码就直接当作最终结果；
              成交额条件不在本地复核范围，直接信任东财自身的数值过滤（见"已知局限"）。
              命中的股票续期，连续多天没再命中的候选超过观察窗口后自动失活，不物理删除、保留历史。
            </p>
          </div>

          <div>
            <div className="text-sm font-semibold text-text-primary mb-1.5">盘中 · 快速刷新</div>
            <p className="text-xs text-text-secondary leading-relaxed">
              独立 API，09:26 定时任务 + 手动按钮触发，目标5-10秒完成：查活跃候选 → 按题材分组跑板块/龙头闸门 →
              批量拉一次实时报价（VWAP/开盘Gap 均有合理性校验，异常值置空退回近似而不是带偏结构判断）→
              状态机判定 → 状态真的发生变化才追加写一条事件日志（连同当时的结构层H1/L1原始快照）。
              自己的文件锁，跟每日全量更新的锁完全分开，两边互不阻塞。
            </p>
          </div>
        </div>

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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2.5">
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

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-x-8">
        <div>
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

        <div>
          <div className="text-sm font-semibold text-text-primary mb-2">主动不做</div>
          {ROADMAP_SKIPPED.map((item) => (
            <div key={item.title} className="text-xs text-text-secondary leading-relaxed">
              <b className="text-text-primary">{item.title}</b> —— {item.body}
            </div>
          ))}
        </div>
        </div>

        <div>
        <div className="mb-5">
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
            多数依赖分钟级行情数据，本仓库目前没有该数据源和同步机制——这是一个新的架构决策
            （数据从哪来、同步频率、保留策略），需要先确定做哪一项、怎么建，而不是直接动手写代码。
          </p>
        </div>

        <div>
          <div className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2">
            弱转强分型（Setup Type） <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-bg-elevated border border-dashed border-bg-border text-text-muted">架构预留</span>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed mb-2">
            外部评审指出当前"一套状态机处理所有股票"其实混合了至少三种性质不同的弱转强（趋势龙头回踩修复/
            情绪龙头断板反抽/深水反核），长期应该拆成独立 Policy。这次只做了最小架构预留，
            <b className="text-text-primary">没有重写生产状态机</b>——当前状态机刚修完几个真实存在的语义 bug，
            在语义还不完全可信的基础上扩展三套 Policy 只会把 bug 一起复制三份。
          </p>
          <ul className="space-y-1.5">
            {ROADMAP_SETUP_TYPE.map((item, i) => (
              <li key={item} className="flex items-start gap-2 text-xs text-text-secondary">
                {i === 0
                  ? <CheckCircle2 className="w-3.5 h-3.5 text-up shrink-0 mt-0.5" />
                  : <ArrowRight className="w-3.5 h-3.5 text-text-muted/50 shrink-0 mt-0.5" />}
                {item}
              </li>
            ))}
          </ul>
        </div>
        </div>
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
