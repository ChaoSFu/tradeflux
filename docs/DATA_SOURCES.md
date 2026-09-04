# 数据源与接口清单

记录本项目实际用到、以及评估过但没用的外部行情接口，**含逐字段实测核实结果**。
新需求要挖数据时先翻这里，别重新逆向一遍。

> 铁律：**改代码前先 dump 原始响应。** 本仓库踩过一次——我读了自己解析器的输出
> 就断言"次数拿不到"，实际原始 payload 里全都有。看解析结果不等于看数据源。

---

## 一、当前在用

### 1.1 K 线（主力）：同花顺官方 fuyao 全市场日K dump

```
GET https://fuyao.aicubes.cn/api/dump/market-dumps/daily-k-10d/download-url
Header: X-api-key: <key>
```

全市场 5,545 只 × 最近 10 个交易日，一个 Parquet，约 1 MB，下载 9 秒。
**替代了逐股拉 K 线的几百次请求**（实测命中 178/182）。

- 返回的是 S3 预签名链接，**只活 5 分钟**，不能缓存 URL
- 列：`thscode / currency / interval / adjusted / date_ms / open_price /
  high_price / low_price / close_price / volume / turnover`
- `adjusted` 固定 `none`（**未复权**）——这对涨停判定是好事，涨停按当日实际成交价算
- **没有换手率**
- 收盘后生成。2026-08-26 实测 17:34 已有当日数据，数值与实时行情逐只核对全对
- 其它两个 dump：`daily-k`（10 年全量）、`adjustment-factors`（复权因子）

代码：`app/services/fuyao_dump.py`

### 1.2 区间涨幅：fuyao 单只历史 K 线

```
GET https://fuyao.aicubes.cn/api/a-share/prices/historical
    ?thscode=600519.SH&interval=1d&start=<ms>&end=<ms>&adjust=forward
```

- **每次只能一个 thscode，不接受逗号**（比腾讯还差，别拿它替逐股拉取）
- `interval` 当前**只支持 `1d`**，没有分钟级
- 这里用 `adjust=forward` 前复权：区间收益问"持有这段赚了多少"，必须含除权除息调整。
  跟 dump 的未复权是两个问题的不同答案，不是不一致
- 实测 12 并发 × 24 次全部 `code=0`，无 4001、无丢包

### 1.3 fuyao 其它可用端点（已核实存在，尚未接入）

| 端点 | 说明 |
|---|---|
| `/api/a-share/calendar/trading-days` | 近一年交易日序列，无入参 —— 可还掉"没有独立交易日历"这笔技术债 |
| `/api/a-share/special-data/limit-up-pool` | 涨停池，字段与东财基本对应，可做备份源 |
| `/api/a-share/special-data/limit-break-pool` | 炸板池，**带换手率** |
| `/api/a-share/special-data/limit-up-ladder` | 涨停梯队 |
| `/api/a-share/special-data/dragon-tiger-list` | 龙虎榜 |
| `/api/a-share/auction/snapshot` | 集合竞价，**带竞价换手率** |
| `/api/a-share/valuations/snapshot` | 只有 PE/PB/PS/PCF，**没有流通股本**，推不出换手率 |
| `/api/meta/tickers/search` / `list` | 代码表（快照接口不返回中文名，要靠它解析）|

**fuyao 的坑**：
- HTTP 状态码恒为 200，业务结果看信封 `code` 字段。只看 status code 会把错误当成功
- `data.timestamp` **不是市场时间**——收盘后仍跟着墙上时钟走（18:03→18:04→18:05 实测）。
  **不能拿它判断"今天收盘了没有"**
- QPS 上限文档没写，只有错误码 `4001 频率超限 | 超过约定 QPS`
- 计费/免费额度文档全无，需在 <https://fuyao.aicubes.cn/admin> 自查

### 1.4 实时行情 / 市场时间：腾讯

```
GET https://qt.gtimg.cn/q=sh600000,sz000001      # 批量，GBK，~ 分隔约90字段
GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,65,qfq
Header: Referer: https://finance.qq.com/
```

字段下标（0-based，2026-08 实测核对）：`1`名称 `2`代码 `3`现价 `4`昨收 `5`今开
`30`行情时间戳 `YYYYMMDDHHMMSS` `33`最高 `34`最低 `36`成交量(手) `37`成交额(万)
`38`换手率 `44`流通市值(亿) `45`总市值(亿)

- **字段 30 是唯一可信的"市场时间"来源**：收盘后停在最后一笔（16:14:52）不再走，
  `bar_is_settled()` 靠它判断"这个交易日收盘了没有"。fuyao 的 timestamp 做不到这点
- 腾讯 K 线接口**盘中就发当日那根未收盘的 bar**（新浪不发）——这是 2026-08-26
  "盘中价冒充收盘价"事故的直接成因
- `qfq` 前复权。停牌期间没有 bar，序列出现跳空是正常的不是缺数据

### 1.5 新浪（K线兜底）

```
GET https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData?symbol=sh600000&scale=240&ma=no&datalen=65
GET https://hq.sinajs.cn/list=sh600000
Header: Referer: https://finance.sina.com.cn
```

- 响应是 `/*注释*/` 开头再跟 JSONP，**不是纯 JSON**（诊断脚本为此误报过一次）
- **盘中不发当日 bar**，收盘后才有
- `volume` 单位是**股**，腾讯和东财是**手**
- 不复权

### 1.6 东财：涨停池 / 炸板池

```
GET https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt:asc&date=20260826
GET https://push2ex.eastmoney.com/getTopicZBPool?...（同上，炸板）
Header: Referer: https://quote.eastmoney.com/
```

**必须带 `ut`**，不带则 `data` 为空对象。**带 `date` 参数，能查历史。**

字段（2026-08-25/26 两次实测，与腾讯行情逐只交叉验证）：

| 字段 | 含义 | 核实 |
|---|---|---|
| `c` / `n` | 代码 / 名称 | 名称可能带空格（`金 螳 螂`），要 strip |
| `m` | 市场 0=深 1=沪 | 与本仓库 market 约定一致 |
| `p` | 最新价 **×1000** | 11450 → 11.45 ✓ |
| `ztp` | 涨停价 **×1000** | 11960 → 11.96（昨收10.87×1.1）✓ |
| `zdp` | 涨跌幅 % | 5.336 ✓ |
| `hs` | **换手率 %** | 10.851，与腾讯完全一致 ✓ |
| `amount` | 成交额（元） | 1218008048 → 12.18亿 ✓ |
| `ltsz` / `tshare` | 流通市值 / 总市值（元） | 11585169540 → 115.85亿 ✓ |
| `lbc` | 连板数 | |
| `zttj` | `{'days':N,'ct':M}` 即"N天M板" | |
| `fbt` / `lbt` | 首次 / 最终封板时间，**HHMMSS 整数** | 92500 = 09:25:00，上午只有5位，不能左填充当字符串处理 |
| `zbc` | 当日炸板次数 | |
| `zf` | 振幅 %（**仅炸板池有**） | |
| `hybk` | 东财行业板块名（仅参考，不用于归组） | |

炸板池**没有 `lbt`**——它收盘就是没封住，本来就没有"最终封板"。

> 2026-08-26 教训：这些字段一直都在，我们的 `parse_zb_pool_row` 却只取了 7 个。
> 后来为了补字段差点去接一个新接口，dump 原始响应才发现现成的就够。

### 1.7 东财：涨停原因（可选补充，覆盖不全）

```
GET https://datacenter.eastmoney.com/securities/api/data/v1/get
    ?reportName=RPT_PCHOT_LIMITLIST_HSDETIAL&...
```

- `SSLIMITUP_TIME` 是**最终封板时间**不是首次涨停时间（41/41 全等于 `lbt`，0 例等于 `fbt`）
- **只有 48 行而涨停池有 65 行，漏 17 只**（含真实涨停的 002821）
- 所以只能当"原因补充"，**绝不能用它决定今天谁涨停了**
- `LIMIT_CONTENT` 里的换行是字面量 `\n` 不是真换行；末尾固定带 AI 免责声明（保留，去掉反而误导）

### 1.8 东财：条件选股（候选池召回的根）

```
GET https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code
```

自然语言选股。强势池 / 涨跌停池 / 成交额榜 / 板块核心召回全靠它。
**fuyao 没有任何等价物**——它挂了，dump 再快也没用，因为不知道该关注哪些股票。

- 返回 `DURATION_LIMIT_UP` 是**"曾触及涨停"含炸板**的次数（603580：收盘涨停15次 +
  触及未封4次 = 19），跟我们本地按收盘价算的口径不同，展示文案必须写明"含炸板"
- `INTERVAL_CHG` 是**真实复合区间收益**，跟 `Stock.pct_change_Nd` 那个"日涨幅简单
  相加"的近似不是一回事（603580近60日：真实 204.85% vs 相加 123.14%，差 80 个点）
- 任一分页失败则整体判不可用，**绝不返回部分结果**
- 不返回"东财如何理解 Prompt"之类的字段，数学型条件必须本地二次校验

### 1.9 其它在用

| 用途 | 端点 |
|---|---|
| 大盘分时 | `push2his.eastmoney.com/api/qt/stock/trends2/get` |
| 涨跌家数分布 | `quotederivates.eastmoney.com/datacenter/updowndistribution`（沪000002+深399002+京899050 三市求和）|
| 板块成分股行情 | `push2delay.eastmoney.com/api/qt/clist/get` |
| F10 核心题材 / 板块关联 | `emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax` |
| 监管异动 | `datacenter.eastmoney.com`（RPT 报表）+ `dycalchis.eastmoney.com/price-anomaly/list` |
| 上证收盘 / 滚动市盈率 | `www.csindex.com.cn/csindex-home/perf/index-perf` |

---

### 1.10 东财：连板天梯（权威梯队，2026-09-03 接入）

```
GET https://datacenter.eastmoney.com/securities/api/data/v1/get
    ?source=SECURITIES&client=APP
    &reportName=RPT_INTSELECTION_MONITORHIS
    &columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,N_CLASS
    &filter=(TRADE_DATE='2026-09-01')(@N_CLASS<>"NULL")(IS_ST="0")
```

- `N_CLASS` = 连板数。**`TRADE_DATE` 是入参，任意历史日期都能取**——这是它最大的价值
- 只返回 **2 板及以上**，不含首板
- `IS_ST="0"` 服务端已排除 ST；但**不排除退市整理期股票**，"退"字股要自己剔
  （2026-07-17 实测：东财最高 5 板，那只是 920305「云创退」，0.40 元的退市股）
- **也不排除北交所**。北交所 30% 涨跌幅，3 连板 = +120%，跟主板 +33% 不可比，
  混进同一个「最高连板」指标会让曲线失去意义，同样要自己剔
- `code=9201「返回数据为空」` 是"那天没有"的明确信号，跟请求失败分开了。
  但**收盘后不是立即发布**：09-03 收盘后仍返回 9201，所以"返回空且我们有连板记录"
  要判为"疑似未发布"而不是"确实没有"
- 它**会漏**（见坑 11），不能当唯一真理

代码：`app/services/limit_up_detail_fetcher.fetch_limit_up_ladder()`

### 1.11 东财：板块指数 K 线（RS_sector 基准）

```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.BK0832&klt=101...
```

- `secid` = `"90." + Sector.code`，实测 BK0832 工业互联网可取 **300 根**
  （2025-06-16 ~ 2026-09-03）
- **不能走 `fetch_index_kline()`**：那个函数是腾讯优先的（为 5 个固定指数定的），
  而腾讯根本没有 BK 板块码，会先拿到畸形数据再在兜底里抛
  `'list' object has no attribute 'get'`——**不是"板块拉不到"，是走错了路**
- push2his 限流很凶（见坑 13），所以只用于一次性回填；日常增量走 clist 的 `f2`
  字段（板块指数点位），零新增请求

代码：`app/services/eastmoney_fetcher.fetch_sector_kline()` /
`app/services/sector_index_service.py`

## 二、评估过但**没有采用**

### 2.1 东财 stockextenddata typelist（2026-08-26 评估，未采用）

```
GET https://stockextenddata.eastmoney.com/api/typelist/get?Fl=0,1,...,37&Ty=4&Ft=&St=0&Sf=4&Vl=1&NFt=3
Header: Referer/Origin: https://emrnweb.eastmoney.com/
        ut: 76ca1a6049d1e54cb4370684a433ac390a80a351
        cid: web + 几个空 header（dn; / st; / uid;）—— 缺了返回 Rc=102、data 为 null
```

对应页面：<https://emrnweb.eastmoney.com/ztzt/Monitor?date=2026-08-25&tabIndex=1>

`Ty=4` 是炸板池（2026-08-26 返回 20 行，与 `getTopicZBPool` 完全一致）。
字段是数字键，已用腾讯行情逐个解码：

| 键 | 含义 | 键 | 含义 |
|---|---|---|---|
| 0 | `市场.代码` | 16 | 首封时间 HHMMSS |
| 1 | 名称 | 17 | 终封时间 |
| 3 | 最新价 **×100** | 27 | 成交额（元）|
| 4 | 涨跌幅 ×100 | 28 | 流通市值（元）|
| 5 / 8 | 连板数 / 连板文本 | 31 | 最后开板时间 |
| 6 / 7 | 板块代码 / 名称 | 32 | 炸板次数 |
| **9** | **换手率 ×100** | 33 | 涨停价 ×100 |
| 14 | 交易日 | 37 | 成交量（手）|

**不采用的原因**：字段是 `getTopicZBPool` 的子集，还少了振幅；**没有 date 参数**
（查不了历史）；依赖写死的 `ut` token 和几个非常规空 header。换过去是净亏。

留在这里是因为 `Ty` 换个值可能是别的池子（涨停/跌停/首板…），将来需要时可以从
这个入口继续挖。

### 2.2 同花顺网页版 hq 接口（2026-08-26 评估，未采用）

```
GET https://d.10jqka.com.cn/v6/line/hs_600519/01/last.js       # single_kline
GET .../multi_last_snapshot                                     # 批量快照，~99 codes/次，带换手率
```

- 不需要 cookie；市场 ID 非标准（16=沪，32/33/34=深，144=北）
- **没有 `multi_kline`**：从前端 JS bundle 里提取过完整端点表，只有
  `multi_last_snapshot` 带 `multi_` 前缀，所有 K 线端点都是 `single_*`
- 另有 `common_hq_aggr_cache` 命名空间（更快：0.19s vs 0.47s，10 并发 20/20）

不采用：`single_kline` 一次一只且不带换手率，`multi_last_snapshot` 的能力腾讯已有，
换过去要重写一套非标准市场 ID 映射，不划算。

### 2.3 前端跨域代理（2026-08-26 评估，明确否决）

设想：浏览器直连东财，结果回传后端。所有东财端点确实都带 CORS 头，技术上可行。

否决理由：**帮不到定时任务**（用户不开页面时照样要跑）；把限流从服务器 IP 转嫁到
用户自己家里的 IP（"这样我没法看盘了"）；后端会变成接受不可信客户端数据写入
交易决策库。

---

## 三、通用约定与反复踩过的坑

1. **失败与"确实没有"必须能分开。** 全仓库反复踩：`turnover_rate=0.0` 冒充"换手率0%"、
   炸板池拉取失败返回 `[]` 被当成"今天没炸板"于是删了 20 条已有数据、
   `fetch_interval_returns` 失败返回 `None` 分不清"请求挂了"和"上市不满60天"。
   规则：**拿不到写 `None` 或抛异常，`0`/`[]` 只表示真实的零。**

2. **"返回了"不等于"拿到了"。** 新浪盘中会返回 K 线但缺当天那根。
   凡是要求某一天的数据，必须校验那一天真的在返回里（`require_date`）。

3. **同一个市场事实只能有一套判定函数。** 已抽出 8 个：`build_kline_bar()`、
   `exact_limit_price()`、`bar_is_settled()`、`thscode_suffix()`、`quote_prefix()`、
   `market_int()`、`compute_rs_sector_from_vendor()`、`board_streaks()` /
   `max_board_in_window()`。不同数据源走同一条判定，否则同一只股票会因为"这根 bar
   从哪来"得出不同的涨停结论。

   **八次里没有一次是设计时就想到的**，每次都是先分叉、再由生产数据暴露。所以现在
   的默认姿势反过来：写一个"数一数连板/算一算涨停价"的循环之前，先搜一遍有没有人
   算过同一件事。允许存在第二个实现，它迟早会跟第一个分叉。

4. **一个数据源的权威性只在它真正覆盖的范围内成立。** 涨跌停选股 API 不含 ST 股，
   拿它的"没返回"推断"ST 股没涨停"是错的。

5. **时间戳要看清是市场时间还是响应时间。** 腾讯字段 30 是市场时间（收盘后不动），
   fuyao 的 `data.timestamp` 是响应时间（一直在走）。判断"收盘了没有"只能用前者。

6. **诊断工具报假警比不报还糟。** 诊断脚本第一版拿东财 Referer 去打新浪被跳转页
   挡掉、又把新浪 K 线的 `/*注释*/` 前缀误判成非 JSON，白白让人去查不存在的问题。

7. **改代码前先 dump 原始响应。**（见文首）

8. **要一个"没有的"字段时，先把现有请求的完整响应打出来看一遍。**
   2026-09-03 要给高标龙头生命周期补成交量、成交额、相对强度、换手率四项，
   最初判定为"数据缺失、需要接新接口"。逐个查证后**四项全部零新增请求**：

   | 以为要新接口 | 实际在哪 |
   |---|---|
   | 成交量 / 成交额 | fuyao dump 的 parquet **本来就有** `volume`/`turnover` 两列，`load_bars` 的 `read_table` 只选了 6 列；腾讯 K 线每行 `row[5]` 也被解析器直接丢掉 |
   | rs_market | `IndexDailySnapshot` 已有 5 指数 × 120 根完整历史 |
   | rs_sector | `Sector.pct_change_5d/10d/20d/60d`（f109/f110/f160/f165）每天在同步 |
   | 换手率 | 涨停池**和炸板池**的 `ltsz`（流通市值）÷ 收盘价 = 流通股本 |

   同一份文档第 1.1 节早就列着 dump 的完整列名。**写在文档里的经验，不主动去查
   还是会重蹈。** 这条要当默认排查顺序，不是"想起来再说"。

9. **两个数据源不一致时，先假设是自己错。**
   2026-09-03 强势池按 `PeakBoard60D>=4` 收窄后，东财返回 61 只、本地重算只有 47 只
   符合。第一反应是"东财理解错了 prompt"——**实测 14 只差异里 11 只是我们自己算错的**
   （`_snapshots_to_klinebars` 在快照缺口处取错前收，把正确的 `is_limit_up=True`
   改成了 False，连板数系统性偏低）。剩下 3 只用腾讯 K 线独立重算确认确实没到 4 板，
   才是真正的口径差异。

10. **内部一致性检查证明不了正确性，必须有独立外部源。**
    `verify_board_history.py` 只对"库里已标记 board_count>=3"的记录重拉 K 线对账，
    是**自指的**——它能证伪"我们说有、实际没有"，却完全看不见"实际有、我们没记"。
    2026-08-06 那天它报「0 分歧」，而东财连板天梯是 10 板、我们只有 5 板，
    整段行情的最高点漏掉了，校验却全绿。
    真正补上这个盲区的是东财连板天梯（`RPT_INTSELECTION_MONITORHIS`，见 1.10）。

11. **权威源也会漏。** 上一条那个天梯接口实测至少 3 例真实连板股没收录
    （002827 08-04、600272 08-12、600540 09-02），每例都用腾讯按交易所精确涨停价
    核对过——收盘价分毫不差等于涨停价。所以修数据时用**三源裁决**：
    我们的值 × 东财天梯 × 腾讯K线重算，两源一致才动，三方不同就如实报冲突不动数据。

12. **"功能做完了"和"数据真的到了"是两回事。**
    接入成交量时，同一个功能连续三次"接了一半"：先是造好 `compute_turnover_rate()`
    没在快照里调用；再是三条取数路径（dump / 腾讯 / DB重建）只接了两条，而日志里
    「DB重建 230 只」说明绝大多数走的正是漏掉那条；最后还漏了行情兜底和历史行回填。
    **每一段单独看都对，断的是端到端的链条，而单元测试全绿。**
    抓到它们的是生产上一条覆盖率查询：

    ```sql
    SELECT date, count(*) 行数, count(volume) 有量,
           count(*) FILTER (WHERE volume_source='dump') 来自dump
    FROM stock_daily_snapshots WHERE date >= CURRENT_DATE - 12
    GROUP BY date ORDER BY date DESC;
    ```

    这类"数据实际到没到"的断言比测代码更有价值。接完任何新字段都该跑一次。

13. **push2 和 push2his 是两个域名，不要混为一谈。**
    `push2.eastmoney.com/api/qt/clist`（列表、板块行情）实测正常——888 个板块 10 页
    8.3 秒。而 `push2his.eastmoney.com/api/qt/stock/kline`（K线历史）限流很凶，
    约十几次请求就会把 IP 打进封锁，且**连既有代码依赖的指数 K 线也一起拉不到**。
    所以设计上要让 push2his 只承担一次性回填，日常增量走 clist。

14. **窗口切片会把连板段从中间切开。** 2026-09-04：`bars[-60:]` 取"近60日"，
    603065 宿迁联盛 06-03~06-12 那段 6 连板正好横跨边界，切完数出 2 板。同一批 bar，
    `identify_leader_cycle` 扫全段报 6 板。

    **被切出来的残段不是一段更短的连板。** 2 这个数不描述任何真实的东西——既不是
    这只票的连板高度，也不是任何一段真实的连板。它还让我们误判了整批强势池的口径
    差异：东财报 ≥4、我们报 <4，一直以为是对方口径松，实际是我们在边界上把段切碎，
    方向恰好是系统性低估。

    规则：**连板段（以及任何"连续 N 天"的事件）是原子的，只要与窗口有交集就算整段。**
    代价是段起点最多比窗口早一个段长，远好过系统性低估。另外若被计入的段顶到了
    `bars[0]`，更早的涨停可能压根没拉进来，这个数只是**下界**
    （`board_count_60d_truncated`）。

    同类要检查的还有：连跌停段、连续下跌天数、任何 `xxx[-N:]` 之后再做游程统计的地方。
