import client from './client'
import type { TradeJournalEntry, TradeJournalList } from '@/types'

export interface TradeJournalPayload {
  stock_code: string
  stock_name?: string | null
  action: string
  trade_time: string
  price: number
  position_pct?: number | null
  reason?: string | null
  planned_stop?: number | null
  target?: number | null
  emotion_tag?: string | null
  note?: string | null
  exit_reason?: string | null
  realized_pnl?: number | null
  pnl_pct?: number | null
}

export const fetchTradeJournal = (params?: {
  page?: number; page_size?: number; stock?: string; action?: string; emotion_tag?: string
}) => client.get<TradeJournalList>('/trade-journal', { params }).then((r) => r.data)

export const createTradeEntry = (body: TradeJournalPayload) =>
  client.post<TradeJournalEntry>('/trade-journal', body).then((r) => r.data)

export const updateTradeEntry = (id: number, body: Partial<TradeJournalPayload>) =>
  client.patch<TradeJournalEntry>(`/trade-journal/${id}`, body).then((r) => r.data)

export const deleteTradeEntry = (id: number) =>
  client.delete(`/trade-journal/${id}`).then((r) => r.data)
