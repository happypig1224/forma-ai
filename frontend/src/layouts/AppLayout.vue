<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Film,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Video,
  AlertTriangle,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const navItems = [
  { name: '工作台', path: '/', icon: LayoutDashboard },
  { name: '制作任务', path: '/tasks', icon: Film },
  { name: '素材与分类', path: '/library', icon: FolderKanban },
]

const currentPath = computed(() => route.path)

const breadcrumbs = computed(() => {
  const crumbs = [{ label: '控制台', path: '/' }]
  if (route.path === '/') {
    crumbs.push({ label: '工作台', path: '/' })
  } else if (route.path.startsWith('/library')) {
    crumbs.push({ label: '素材与分类', path: '/library' })
  } else if (route.path.startsWith('/tasks')) {
    crumbs.push({ label: '制作任务', path: '/tasks' })
    if (route.params.id) {
      crumbs.push({ label: `任务 #${route.params.id}`, path: route.fullPath })
    }
  }
  return crumbs
})

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}
</script>

<template>
  <div class="flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-800 font-sans">
    <!-- Sidebar -->
    <aside class="flex w-56 flex-col border-r border-slate-200 bg-white shrink-0">
      <!-- Brand -->
      <div class="flex h-14 items-center gap-2.5 border-b border-slate-200 px-4 bg-white">
        <div class="flex h-8 w-8 items-center justify-center rounded-md bg-blue-600 text-white shrink-0">
          <Video class="h-4 w-4" />
        </div>
        <div class="flex flex-col">
          <span class="font-bold text-sm tracking-tight text-slate-900 leading-none">FormaAI</span>
          <span class="text-[11px] text-slate-400 mt-0.5 leading-none">视频协同平台</span>
        </div>
      </div>

      <!-- Navigation Links -->
      <nav class="flex-1 overflow-y-auto p-2.5 space-y-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-2.5 rounded-md px-3 py-2 text-xs font-medium transition-colors"
          :class="[
            currentPath === item.path || (item.path !== '/' && currentPath.startsWith(item.path))
              ? 'bg-blue-50 text-blue-700 font-semibold'
              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
          ]"
        >
          <component
            :is="item.icon"
            class="h-4 w-4 shrink-0"
            :class="[
              currentPath === item.path || (item.path !== '/' && currentPath.startsWith(item.path))
                ? 'text-blue-600'
                : 'text-slate-400'
            ]"
          />
          <span>{{ item.name }}</span>
        </RouterLink>
      </nav>

      <!-- Account Cancellation Notice -->
      <div v-if="authStore.isDisabling" class="m-2.5 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
        <div class="flex items-center gap-1.5 font-semibold">
          <AlertTriangle class="h-3.5 w-3.5 shrink-0 text-amber-600" />
          <span>账号注销中</span>
        </div>
        <p class="mt-1 text-[11px] text-amber-700">冷静期内仅支持数据查看，禁止新建任务。</p>
      </div>

      <!-- User footer -->
      <div class="border-t border-slate-200 p-2.5">
        <div class="flex items-center justify-between gap-2 rounded-md p-1.5 hover:bg-slate-50 transition">
          <div class="flex items-center gap-2 overflow-hidden min-w-0">
            <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-700 font-semibold text-xs border border-slate-200">
              {{ (authStore.user?.email || 'U')[0].toUpperCase() }}
            </div>
            <div class="truncate text-xs text-slate-700" :title="authStore.user?.email || ''">
              {{ authStore.user?.email || 'user' }}
            </div>
          </div>
          <button
            @click="handleLogout"
            title="退出登录"
            class="rounded p-1 text-slate-400 hover:text-slate-700 transition shrink-0"
          >
            <LogOut class="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex flex-1 flex-col overflow-hidden">
      <!-- Top Bar -->
      <header class="flex h-14 items-center border-b border-slate-200 bg-white px-6 shrink-0">
        <!-- Breadcrumb -->
        <div class="flex items-center gap-1.5 text-xs text-slate-500">
          <template v-for="(crumb, idx) in breadcrumbs" :key="crumb.path">
            <span
              v-if="idx > 0"
              class="text-slate-300"
            >
              /
            </span>
            <span
              :class="idx === breadcrumbs.length - 1 ? 'font-semibold text-slate-800' : 'text-slate-500'"
            >
              {{ crumb.label }}
            </span>
          </template>
        </div>
      </header>

      <!-- Page Content View -->
      <main class="flex-1 overflow-y-auto p-6 bg-slate-50">
        <div class="mx-auto max-w-7xl">
          <RouterView />
        </div>
      </main>
    </div>
  </div>
</template>
