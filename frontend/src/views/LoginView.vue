<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Video,
  Mail,
  Lock,
  Loader2,
  Eye,
  EyeOff,
} from 'lucide-vue-next'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { parseApiError } from '@/utils/errors'

const router = useRouter()
const authStore = useAuthStore()

const activeTab = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const showPassword = ref(false)
const errorMessage = ref('')
const isAlreadyRegistered = ref(false)
const isSubmitting = ref(false)

function handleTabChange(tab: 'login' | 'register') {
  activeTab.value = tab
  errorMessage.value = ''
  isAlreadyRegistered.value = false
}

async function handleSubmit() {
  errorMessage.value = ''
  isAlreadyRegistered.value = false
  isSubmitting.value = true
  try {
    if (activeTab.value === 'login') {
      await authStore.login(email.value, password.value)
    } else {
      const res = await authApi.register({
        email: email.value,
        password: password.value,
      })
      authStore.setToken(res.data.access_token)
      const meRes = await authApi.getCurrentUser()
      authStore.user = meRes.data
    }
    router.push('/')
  } catch (err: any) {
    if (err.response?.status === 409) {
      isAlreadyRegistered.value = true
    }
    errorMessage.value = parseApiError(
      err,
      activeTab.value === 'login' ? '登录失败，请检查账号和密码' : '注册失败，请核对信息'
    )
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen w-screen items-center justify-center bg-slate-50 px-4 py-12 text-slate-800 font-sans">
    <div class="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 shadow-xs">
      <!-- Brand Header -->
      <div class="flex flex-col items-center text-center mb-6">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-white mb-2.5">
          <Video class="h-5 w-5" />
        </div>
        <h1 class="text-lg font-bold tracking-tight text-slate-900">FormaAI</h1>
        <p class="text-xs text-slate-500 mt-0.5">视频协同生产平台</p>
      </div>

      <!-- Tab Switcher -->
      <div class="grid grid-cols-2 gap-1 rounded bg-slate-100 p-1 text-xs font-medium mb-5">
        <button
          type="button"
          @click="handleTabChange('login')"
          :class="[
            activeTab === 'login'
              ? 'bg-white text-slate-900 font-semibold shadow-xs'
              : 'text-slate-500 hover:text-slate-800'
          ]"
          class="rounded py-1.5 transition text-center"
        >
          登录
        </button>
        <button
          type="button"
          @click="handleTabChange('register')"
          :class="[
            activeTab === 'register'
              ? 'bg-white text-slate-900 font-semibold shadow-xs'
              : 'text-slate-500 hover:text-slate-800'
          ]"
          class="rounded py-1.5 transition text-center"
        >
          注册
        </button>
      </div>

      <!-- Error Feedback -->
      <div
        v-if="errorMessage"
        class="mb-4 rounded border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-700"
      >
        <div class="flex items-center justify-between gap-2">
          <span>{{ errorMessage }}</span>
          <button
            v-if="isAlreadyRegistered && activeTab === 'register'"
            type="button"
            @click="handleTabChange('login')"
            class="font-semibold text-rose-800 underline hover:no-underline shrink-0"
          >
            去登录
          </button>
        </div>
      </div>

      <!-- Form -->
      <form @submit.prevent="handleSubmit" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-700 mb-1">电子邮箱</label>
          <div class="relative">
            <Mail class="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
            <input
              v-model="email"
              type="email"
              required
              autocomplete="email"
              placeholder="name@company.com"
              class="enterprise-input pl-8 text-xs"
            />
          </div>
        </div>

        <div>
          <label class="block text-xs font-medium text-slate-700 mb-1">密码</label>
          <div class="relative">
            <Lock class="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
            <input
              v-model="password"
              :type="showPassword ? 'text' : 'password'"
              required
              autocomplete="current-password"
              placeholder="••••••••"
              class="enterprise-input pl-8 pr-8 text-xs"
            />
            <button
              type="button"
              @click="showPassword = !showPassword"
              class="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-600 transition"
              tabindex="-1"
            >
              <EyeOff v-if="showPassword" class="h-4 w-4" />
              <Eye v-else class="h-4 w-4" />
            </button>
          </div>
        </div>

        <button
          type="submit"
          :disabled="isSubmitting"
          class="btn-primary w-full py-2 text-xs font-semibold mt-2"
        >
          <Loader2 v-if="isSubmitting" class="h-3.5 w-3.5 animate-spin" />
          <span>{{ activeTab === 'login' ? '登录' : '注册' }}</span>
        </button>
      </form>
    </div>
  </div>
</template>
