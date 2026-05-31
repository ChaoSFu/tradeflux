import client from './client'
import type { SignalListResponse } from '@/types'

export const fetchSignals = (params?: {
  page?: number
  page_size?: number
  signal_type?: string
  risk_level?: string
  stock_id?: number
  sector_id?: number
}) => client.get<SignalListResponse>('/signals', { params }).then((r) => r.data)
