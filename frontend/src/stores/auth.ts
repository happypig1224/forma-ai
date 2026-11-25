import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi, type UserAccount } from '@/api/auth'
import { getAccessToken, setAccessToken } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserAccount | null>(null)
  const token = ref<string | null>(getAccessToken())
  const loading = ref(false)
  const initialized = ref(false)

  const isAuthenticated = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isDisabling = computed(() => user.value?.status === 'disabling')

  async function initialize(): Promise<void> {
    if (initialized.value) return
    if (token.value) {
      try {
        const res = await authApi.getCurrentUser()
        user.value = res.data
      } catch {
        setToken(null)
        user.value = null
      }
    }
    initialized.value = true
  }

  function setToken(newToken: string | null): void {
    token.value = newToken
    setAccessToken(newToken)
  }

  async function login(email: string, pass: string): Promise<void> {
    loading.value = true
    try {
      const res = await authApi.login(email, pass)
      setToken(res.data.access_token)
      const meRes = await authApi.getCurrentUser()
      user.value = meRes.data
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // Ignore network errors on logout
    } finally {
      setToken(null)
      user.value = null
    }
  }

  return {
    user,
    token,
    loading,
    initialized,
    isAuthenticated,
    isAdmin,
    isDisabling,
    initialize,
    login,
    logout,
    setToken,
  }
})
