/**
 * 模型说明 —— 整个 V2 的决策链、每道 gate 的规则、以及**现在做不到什么**。
 *
 * 这一页存在的理由：V2 的全部价值在于可解释。如果人看不懂 SIGNAL READY 是
 * 怎么来的，那它跟一个 83 分的黑箱没有区别。
 */
const CHAIN = [
  ['市场 Market', '指数趋势是否多头',
   ['核心指数：上证 / 深成指 / 创业板 / 科创50',
    '用后端 market-trend 已算好的 state / alignment / above_maN / MA20斜率',
    'BLOCK：过半核心指数 bearish 或 weak',
    'PASS：过半 bullish 或 strong，且没有任何一个走坏',
    'WAIT：其余，含"拿不到数据"',
    '刻意不用情绪温度——那是自造的复合分，说不清由什么构成']],
  ['主线 Mainline', '板块是否多头 + 是否有注意力',
   ['趋势：目前判不出来 —— 板块没有均线数据',
    '领先证据：5/10/20日涨幅排名、涨停数排名、最高连板、强势股数排名、成交额',
    '排序是字典序（证据条数 → 最高板 → 成交额），不是主线分',
    '绝不用涨幅冒充均线多头 —— 涨幅为正只说明这段涨了，说不出结构']],
  ['核心股 Core Stock', '三种不同的 Leadership',
   ['活跃龙头：生命周期状态机 price_v1_1（透明状态机，不是评分）',
    '涨停核心：按连板梯队排，Leadership 与 Tradability 分开',
    '趋势龙头：成交额容量；趋势暂时判不出来（缺个股均线）',
    '「重点跟踪」≠ 可以买——生命周期回答"它在哪"，回答不了"能不能买"']],
  ['监管 Regulation', '个股风险 + 市场环境',
   ['个股：监管中 / 即将解除 / 最近解除 / 逼近严重异动阈值',
    '市场级监管态度：暂无结构化数据',
    '不能用个股监管数量推断管理层态度——那两件事的含义可能完全相反']],
]

const PRINCIPLES = [
  ['Facts First', '优先展示可核对的市场事实：收盘、均线、排列、斜率、涨跌幅、成交额、'
    + '换手、连板数、涨停次数、排名、监管状态。'],
  ['Unknown ≠ False', 'None 不是 0，缺失不是"弱"，判不出来不是"不通过"。缺数据时显示 '
    + '「—」「数据不足」「无法判断」，绝不用 0 / False / 持平顶替。'],
  ['标准指标优先', 'leader_score / risk_score / emotion_score / 情绪温度这些自造指标'
    + '在 V2 里不作为核心排序依据，也不作为任何 gate 的条件。'],
  ['没有黑箱综合分', '决策链只输出 PASS / WAIT / BLOCK 和具体原因，不输出 0~100。'
    + '一个 83 分说不清它是"三项满足一项差一点"还是"四项都刚过线"，'
    + '而这两种情况的交易含义完全不同。'],
  ['标签必须可还原', '「多头」「主线」「走弱」这类词都必须能对回明确的字段和规则，'
    + '不能来自一个说不清构成的分数。'],
]

const GAPS = [
  ['板块均线', 'SectorIndexDaily 只有当天一根，历史 K 线回填被数据源（push2his）限流'
    + '拦着。所以主线的「趋势 gate」现在给不出 PASS。'],
  ['个股趋势（成交额榜）', '成交额概览接口不带均线字段。成交额大 ≠ 趋势多头，'
    + '所以趋势龙头那一列现在是「数据不足」。'],
  ['市场级监管态度', '需要政策/表态类的结构化数据源，目前一条都没有。'],
  ['历史时点成分股', '生命周期的历史快照是「今天在池子里的股票，当时长什么样」，'
    + '不是当时真正的池子。研究结论只在"今天的幸存者"这个宇宙内成立。'],
]

export default function ModelGuide() {
  return (
    <div className="space-y-4 max-w-4xl">
      <section className="card p-4">
        <h2 className="text-sm text-text-primary">决策链</h2>
        <pre className="mt-2 text-[11px] text-text-secondary leading-relaxed font-mono">
{`市场 Market
  ↓
主线 Mainline
  ↓
核心股 Core Stock
  ↓
监管 Regulation
  ↓
SIGNAL READY
  ↓
人工执行 Human Execution`}
        </pre>
        <div className="mt-3 p-3 rounded bg-bg-elevated text-[12px]">
          <div className="text-text-primary font-medium">SIGNAL READY 是什么</div>
          <div className="text-text-secondary mt-1">
            指数、板块、核心个股、监管四项同时满足，<span className="text-text-primary">
            可以进入人工买点确认阶段</span>。
          </div>
          <div className="text-warn mt-2">
            ≠ 自动交易　≠ 预测上涨　≠ 盈利保证。系统不下单，买点由人确认。
          </div>
        </div>
      </section>

      {CHAIN.map(([name, q, rules]) => (
        <section key={name as string} className="card p-4">
          <h3 className="text-sm text-text-primary">{name}</h3>
          <p className="text-[11px] text-text-muted mt-0.5">回答：{q}</p>
          <ul className="mt-2 space-y-1 text-[11px] text-text-secondary">
            {(rules as string[]).map((r) => <li key={r}>· {r}</li>)}
          </ul>
        </section>
      ))}

      <section className="card p-4">
        <h3 className="text-sm text-text-primary">设计原则</h3>
        <dl className="mt-2 space-y-2">
          {PRINCIPLES.map(([k, v]) => (
            <div key={k}>
              <dt className="text-[12px] text-accent">{k}</dt>
              <dd className="text-[11px] text-text-secondary">{v}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="card p-4 border border-warn/30">
        <h3 className="text-sm text-warn">现在做不到什么</h3>
        <p className="text-[11px] text-text-muted mt-0.5">
          这一节跟上面几节同等重要。系统敢说自己缺什么，它说自己有什么的时候才可信。
        </p>
        <dl className="mt-2 space-y-2">
          {GAPS.map(([k, v]) => (
            <div key={k}>
              <dt className="text-[12px] text-text-primary">{k}</dt>
              <dd className="text-[11px] text-text-secondary">{v}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}
