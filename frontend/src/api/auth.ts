import { apiClient } from './client'

export interface UserAccount {
  id: number
  email: string
  role: 'user' | 'admin'
  status: 'active' | 'disabling' | 'disabled' | 'deleted'
  email_verified_at: string | null
  created_at: string
  updated_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface RegisterRequest {
  email: string
  password: string
}

export const authApi = {
  register(data: RegisterRequest) {
    return apiClient.post<TokenResponse>('/auth/register', data)
  },
  login(email: string, password: string) {
    return apiClient.post<TokenResponse>('/auth/login', { email, password })
  },
  logout() {
    return apiClient.post<void>('/auth/logout')
  },
  getCurrentUser() {
    return apiClient.get<UserAccount>('/auth/me')
  },
}
