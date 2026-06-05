export function fmt(n: number | null | undefined, decimals = 1): string {
  if (n === null || n === undefined) return '--'
  return n.toFixed(decimals)
}

export function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '--'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

export function pctColor(n: number | null | undefined): string {
  if (n === null || n === undefined) return 'text-text-secondary'
  if (n > 0) return 'text-up'
  if (n < 0) return 'text-down'
  return 'text-text-secondary'
}

export const PHASE_COLORS: Record<number, string> = {
  0: '#505570',
  1: '#4F9CF9',
  2: '#FF4560',
  3: '#FFD700',
  4: '#F59E0B',
  5: '#26C281',
  6: '#6B2B3B',
}

export const PHASE_LABELS_EN: Record<number, string> = {
  0: 'Stealth',
  1: 'Initiation',
  2: 'Expansion',
  3: 'Euphoria',
  4: 'Divergence',
  5: 'Decline',
  6: 'Dead Zone',
}

export const PHASE_LABELS_ZH: Record<number, string> = {
  0: '隐匿期',
  1: '启动期',
  2: '扩张期',
  3: '高潮期',
  4: '分歧期',
  5: '衰退期',
  6: '死亡区',
}

export const PHASE_NAME_TO_NUM: Record<string, number> = {
  stealth: 0,
  initiation: 1,
  expansion: 2,
  euphoria: 3,
  divergence: 4,
  decline: 5,
  dead_zone: 6,
}

// Stock-level phase (破位/走弱/normal)
export const STOCK_PHASE_LABELS: Record<string, string> = {
  broken: '破位龙头',
  weakening: '走弱龙头',
  normal: '正常',
}

export const STOCK_PHASE_COLORS: Record<string, string> = {
  broken: '#26C281',    // green (下跌破位)
  weakening: '#34D399', // green (走弱也是亏钱效应，A股惯例用绿)
  normal: '#FF4560',    // red (强势上涨)
}

export const MARKET_PHASE_LABELS: Record<string, string> = {
  bull_frenzy: '疯牛行情',
  warm: '偏暖格局',
  neutral: '震荡分化',
  caution: '谨慎偏弱',
  bear_fear: '熊市恐慌',
}

export const EMOTION_CYCLE_LABELS: Record<string, string> = {
  euphoric: '高潮亢奋',
  heating: '加速升温',
  awakening: '情绪回暖',
  cooling: '情绪降温',
  dormant: '低迷蛰伏',
  cold: '极度冰冷',
}

export const ACTION_LABELS: Record<string, string> = {
  observe: '观察',
  watchlist: '关注',
  low_position_trial: '轻仓试探',
  hold: '持有',
  reduce: '减仓',
  avoid: '回避',
}

export const ACTION_COLORS: Record<string, string> = {
  observe: 'text-text-secondary',
  watchlist: 'text-accent',
  low_position_trial: 'text-up',
  hold: 'text-up',
  reduce: 'text-warn',
  avoid: 'text-down',
}

export const SIGNAL_TYPE_LABELS: Record<string, string> = {
  weak_to_strong: '弱转强',
  broken_board_recovery: '炸板修复',
  divergence_repair: '分歧修复',
  rebound_acceleration: '反弹加速',
  sector_repair_sync: '板块修复',
  emotional_recovery: '情绪修复',
  dragon_leader_change: '龙头切换',
}

export const RISK_COLORS: Record<string, string> = {
  low: 'text-up',
  medium: 'text-warn',
  high: 'text-down',
}

export const RISK_LABELS: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

export const LEADER_TYPE_LABELS: Record<string, string> = {
  overall: '总龙头',
  emotion: '情绪龙',
  trend: '趋势龙',
  compensation: '补涨龙',
  mid_cap: '中盘核心',
}
