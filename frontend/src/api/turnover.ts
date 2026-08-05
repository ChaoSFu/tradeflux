import client from './client'
import type { TurnoverOverviewResponse } from '@/types'

export const fetchTurnoverOverview = (refresh = false) =>
  client
    .get<TurnoverOverviewResponse>('/turnover/overview', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)
