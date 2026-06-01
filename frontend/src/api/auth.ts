import client from './client'

export interface LoginResponse {
  access_token: string
  token_type: string
  username: string
}

export const login = (username: string, password: string) =>
  client.post<LoginResponse>('/auth/login', { username, password }).then(r => r.data)

export const fetchMe = () =>
  client.get<{ username: string; is_admin: boolean }>('/auth/me').then(r => r.data)
