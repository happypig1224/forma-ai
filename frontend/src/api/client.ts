import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

// Create standardized Axios instance
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true, // Send httpOnly refresh cookies if configured
  headers: {
    'Content-Type': 'application/json',
    'X-CSRF-Protection': '1',
  },
})

// Access token management in memory / storage
let currentAccessToken: string | null = localStorage.getItem('formaai_access_token')

export function setAccessToken(token: string | null): void {
  currentAccessToken = token
  if (token) {
    localStorage.setItem('formaai_access_token', token)
  } else {
    localStorage.removeItem('formaai_access_token')
  }
}

export function getAccessToken(): string | null {
  return currentAccessToken
}

// Request interceptor: attach bearer token
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (currentAccessToken && config.headers) {
    config.headers.Authorization = `Bearer ${currentAccessToken}`
  }
  return config
})

// Response interceptor: handle 401 and refresh token logic
let isRefreshing = false
let refreshSubscribers: Array<(token: string) => void> = []

function onRefreshed(token: string): void {
  refreshSubscribers.forEach((callback) => callback(token))
  refreshSubscribers = []
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // Avoid infinite loop on auth endpoints
    const isAuthEndpoint =
      originalRequest?.url?.includes('/auth/login') ||
      originalRequest?.url?.includes('/auth/refresh') ||
      originalRequest?.url?.includes('/auth/register')

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry && !isAuthEndpoint) {
      if (isRefreshing) {
        return new Promise((resolve) => {
          refreshSubscribers.push((token: string) => {
            if (originalRequest.headers) {
              originalRequest.headers.Authorization = `Bearer ${token}`
            }
            resolve(apiClient(originalRequest))
          })
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        // Attempt silent token refresh
        const res = await axios.post(
          '/api/auth/refresh',
          {},
          { withCredentials: true }
        )
        const newAccessToken = res.data.access_token
        setAccessToken(newAccessToken)
        onRefreshed(newAccessToken)
        isRefreshing = false

        if (originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
        }
        return apiClient(originalRequest)
      } catch (refreshErr) {
        isRefreshing = false
        refreshSubscribers = []
        setAccessToken(null)
        window.dispatchEvent(new CustomEvent('formaai:unauthorized'))
        return Promise.reject(refreshErr)
      }
    }

    return Promise.reject(error)
  }
)
