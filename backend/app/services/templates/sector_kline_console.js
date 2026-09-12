// TradeFlux 板块指数日线导出脚本——由「数据体检」页面按当前缺口生成（__DATE__），已填好 __COUNT__ 个板块
//
// 用法：打开东财行情页（比如 https://quote.eastmoney.com/center/boardlist.html）
//       → 先在页面空白处点一下（免得浏览器拦「下载多个文件」）→ F12 → Console → 粘贴整段 → 回车
//
// 节奏（2026-09-12 实测的限流经验）：约 2 秒一个、连取 20 个就被掐，至少封二十分钟，浏览器也一样。
// 所以起步 15 秒一个；被掐就停下等 10～30 分钟再试并自动放慢；连续 30 个顺利再慢慢提速；
// 连着被掐超过 3 小时才放弃。取完（或中途停下）下载一个 sector_klines_<日期>_<时分>.jsonl。
// 电脑别睡、标签页别关。屏幕熄了或标签页在后台也能跑，只是浏览器会把它限到大约一分钟一个。
// 随时在 Console 里输入：__tfStatus() 看进度；__tfSave() 先把已取到的下载下来；__tfStop = true 停下（停前会下载）。
//
// 下一步：把文件传到服务器的收件箱（数据体检页面上有现成的 scp 命令），回页面点「试跑导入」「确认导入」。
(async () => {
  if (window.__tfRunning) { console.warn('已经在跑了。看进度：__tfStatus()；要停：__tfStop = true'); return; }
  const ALL = __CODES__;
  const DATE = '__DATE__';
  if (!ALL.length) { console.log('没有要导出的板块——数据体检里「板块指数日线」这一项是齐的'); return; }
  // 停下后在同一页面再粘贴**同一份**脚本：只取上次剩下的；换了一份新脚本就取它自己的清单
  const KEY = `${DATE}:${ALL.length}:${ALL[0]}:${ALL[ALL.length - 1]}`;
  const CODES = (window.__tfKey === KEY && window.__tfRemaining && window.__tfRemaining.length)
    ? window.__tfRemaining : ALL;
  const UT = 'fa5fd1943c7b386f172d6893dbfba10b';   // 行情页自己带的公开参数
  const DAYS = 300;
  const GAP_START = 15, GAP_MIN = 8, GAP_MAX = 90;   // 两次请求的间隔（秒），自动调
  const WAITS = [10, 15, 20, 30, 30, 30, 30, 30];    // 连着被掐时每次等多久（分钟），全等完还不通就停
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 能被 __tfStop 打断的等待；按墙上时钟算，标签页在后台被浏览器限速也不会等错
  const nap = async (ms) => {
    const end = Date.now() + ms;
    while (!window.__tfStop && Date.now() < end) await sleep(Math.min(1000, end - Date.now()));
  };
  const hhmm = (t) => new Date(t).toTimeString().slice(0, 5);
  const jsonp = (code) => new Promise((resolve) => {
    const cb = `jQuery3510${String(Math.random()).slice(2, 12)}_${Date.now()}`;
    const s = document.createElement('script');
    // 超时后才到的响应也要有地方落，不然页面会报「不是函数」
    const done = (v) => { clearTimeout(t); window[cb] = () => {}; s.remove(); resolve(v); };
    const t = setTimeout(() => done({ err: '超时' }), 15000);
    window[cb] = (d) => done({ data: d });
    s.onerror = () => done({ err: '网络错误' });
    const q = new URLSearchParams({ cb, secid: `90.${code}`, ut: UT, fields1: 'f1,f2,f3,f4,f5,f6',
      fields2: 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61', klt: '101', fqt: '1', end: '20500101',
      lmt: String(DAYS), _: String(Date.now()) });
    s.src = `https://push2his.eastmoney.com/api/qt/stock/kline/get?${q}`;
    document.head.appendChild(s);
  });
  // 跟服务端 _parse_sector_klines 同一套字段：日期,开,收,高,低,量,额,振幅,涨跌幅
  const parse = (lines) => lines.map((l) => l.split(',')).filter((p) => p.length >= 9)
    .map((p) => ({ date: p[0], open: +p[1], close: +p[2], high: +p[3], low: +p[4],
                   volume: +p[5], amount: +p[6], pct_change: +p[8] }))
    .filter((r) => r.date && Number.isFinite(r.close) && r.close > 0);
  const save = (rows, name) => {
    const blob = new Blob(rows.map((o) => JSON.stringify(o) + '\n'), { type: 'application/x-ndjson' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
  };

  const todo = [...CODES], out = [], noData = [];
  const total = todo.length, t0 = Date.now(), tag = hhmm(t0).replace(':', '');
  let gap = GAP_START, fails = 0, okStreak = 0;
  window.__tfKey = KEY;
  window.__tfRemaining = todo;   // 边取边更新：停下后在同一页面再粘贴，只取剩下的
  window.__tfStop = false;
  window.__tfRunning = true;
  // 东财没有指数日线的板块也写一条空记录：导入时记下来，体检就不会每天把它当缺口报
  const dump = () => [...out, ...noData.map((code) => ({ code, rows: [] }))];
  const eta = () => Math.max(1, Math.round(todo.length * (gap + 1) / 60));
  window.__tfStatus = () => console.log(`已取 ${out.length}，无数据 ${noData.length}，还剩 ${todo.length}/${total}`
    + `｜现在每 ${gap} 秒一个｜已跑 ${Math.round((Date.now() - t0) / 60000)} 分钟，顺利的话还要约 ${eta()} 分钟`);
  window.__tfSave = () => {
    const rows = dump();
    if (rows.length) save(rows, `sector_klines_${DATE}_${tag}_${out.length}.jsonl`);
    else console.log('还没取到任何板块');
  };
  console.log(`开始：${total} 个板块，起步每 ${gap} 秒一个（顺利的话约 ${eta()} 分钟）。被掐会自己停下等、再放慢，不用管它。`);
  try {
    while (todo.length && !window.__tfStop) {
      const code = todo[0];
      const r = await jsonp(code);
      if (r.err) {
        okStreak = 0;
        if (fails >= WAITS.length) {
          console.error(`连着被掐 ${fails + 1} 次、前后等了 ${WAITS.reduce((a, b) => a + b)} 分钟还不通，先停手`);
          break;
        }
        const w = WAITS[fails++];
        gap = Math.min(GAP_MAX, Math.round(gap * 1.5));
        console.warn(`${hhmm(Date.now())} ${code} 被掐（${r.err}）。等 ${w} 分钟，`
          + `${hhmm(Date.now() + w * 60000)} 再试，之后放慢到每 ${gap} 秒一个`);
        await nap(w * 60000);
        continue;
      }
      fails = 0;
      todo.shift();
      const kl = (r.data && r.data.data && r.data.data.klines) || [];
      if (kl.length) {
        out.push({ code, rows: parse(kl) });
        okStreak++;
        if (out.length % 10 === 0) window.__tfStatus();
      } else {
        noData.push(code);
        console.log(`${code} 无数据（这个板块没有指数日线）`);
      }
      if (okStreak >= 30 && gap > GAP_MIN) {
        gap = Math.max(GAP_MIN, Math.round(gap * 0.8));
        okStreak = 0;
        console.log(`连续 30 个都顺利，提速到每 ${gap} 秒一个`);
      }
      if (todo.length) await nap(gap * 1000 * (0.8 + Math.random() * 0.4));
    }
  } catch (e) {
    console.error('脚本出错：', e);
  } finally {
    window.__tfRunning = false;
    const name = `sector_klines_${DATE}_${tag}${todo.length ? '_part' : ''}.jsonl`;
    const rows = dump();
    if (rows.length) save(rows, name);
    window.__tfRemaining = todo.length ? todo : undefined;
    console.log(`${hhmm(Date.now())} 结束：成功 ${out.length} 个板块${rows.length ? `（已下载 ${name}）` : ''}，`
      + `无数据 ${noData.length} 个，用时 ${Math.round((Date.now() - t0) / 60000)} 分钟`);
    if (todo.length) console.error(`还有 ${todo.length} 个没取到——在同一个页面（别刷新）再粘贴同一份脚本，会只取这些`);
    else console.log('全部取完 ✓ 下一步：把文件传到服务器收件箱，回数据体检页面导入');
  }
})();
