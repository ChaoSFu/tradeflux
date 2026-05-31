import client from './client'
import type { DailyReview, DailyReviewListResponse } from '@/types'

export const fetchReviews = (params?: { page?: number; page_size?: number }) =>
  client.get<DailyReviewListResponse>('/reviews', { params }).then((r) => r.data)

export const fetchLatestReview = () =>
  client.get<DailyReview>('/reviews/latest').then((r) => r.data)

export const fetchReviewByDate = (date: string) =>
  client.get<DailyReview>(`/reviews/${date}`).then((r) => r.data)

export const generateTodayReview = () =>
  client.post<DailyReview>('/reviews/generate-today').then((r) => r.data)
