<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Film,
  FolderKanban,
  Plus,
  ArrowRight,
  CheckCircle2,
  PlayCircle,
} from 'lucide-vue-next'
import { libraryApi, type Category } from '@/api/library'
import { productionApi, type VideoTask } from '@/api/production'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const tasks = ref<VideoTask[]>([])
const categories = ref<Category[]>([])
const loading = ref(true)

const runningCount = computed(() => tasks.value.filter((t) => t.stage !== 'finished').length)
const finishedCount = computed(() => tasks.value.filter((t) => t.stage === 'finished').length)

const CONTENT_TYPE_LABELS: Record<string, string> = {
  commerce: '电商带货',
  brand: '品牌宣传',
  knowledge: '知识干货',
  story: '剧情故事',
}

function getStageLabel(stage: string): string {
  const map: Record<string, string> = {
    created: '初始输入',
    research: '商业调研',
    script_proposal: '方案选择',
    script_confirmed: '规格锁定',
    keyframes: '分镜规划',
    shot_video: '镜头制作',
    composition: '成片合成',
    finished: '已归档',
  }
  return map[stage] || stage
}

function getStatusBadge(status: string) {
  switch (status) {
    case 'success':
      return { label: '已完成', class: 'badge-success' }
    case 'running':
      return { label: '进行中', class: 'badge-info' }
    case 'waiting_confirmation':
      return { label: '待确认', class: 'badge-warning' }
    default:
      return { label: status, class: 'badge-neutral' }
  }
}

function formatDate(iso: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(async () => {
  try {
    const [taskRes, catRes] = await Promise.all([
      productionApi.listTasks(),
      libraryApi.listCategories(),
    ])
    tasks.value = taskRes.data.items
    categories.value = catRes.data.items
  } catch (err) {
    console.error('Failed to load dashboard metrics', err)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-5">
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div>
        <h1 class="text-lg font-bold text-slate-900 tracking-tight">工作台</h1>
        <p class="text-xs text-slate-500 mt-0.5">短视频制作任务与品类资产概览</p>
      </div>

      <div class="flex items-center gap-2.5">
        <button
          @click="router.push('/library')"
          class="btn-secondary text-xs"
        >
          <FolderKanban class="h-3.5 w-3.5" />
          <span>分类管理</span>
        </button>
        <button
          @click="router.push('/tasks?create=true')"
          :disabled="authStore.isDisabling"
          class="btn-primary text-xs"
        >
          <Plus class="h-3.5 w-3.5" />
          <span>新建任务</span>
        </button>
      </div>
    </div>

    <!-- 4 Real Metric Cards -->
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div class="enterprise-card p-4">
        <span class="text-xs font-medium text-slate-500">任务总数</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-bold text-slate-900 font-mono tabular-nums">{{ tasks.length }}</span>
          <Film class="h-4 w-4 text-slate-400" />
        </div>
      </div>

      <div class="enterprise-card p-4">
        <span class="text-xs font-medium text-slate-500">进行中任务</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-bold text-blue-600 font-mono tabular-nums">{{ runningCount }}</span>
          <PlayCircle class="h-4 w-4 text-blue-500" />
        </div>
      </div>

      <div class="enterprise-card p-4">
        <span class="text-xs font-medium text-slate-500">已完成任务</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-bold text-emerald-600 font-mono tabular-nums">{{ finishedCount }}</span>
          <CheckCircle2 class="h-4 w-4 text-emerald-500" />
        </div>
      </div>

      <div class="enterprise-card p-4">
        <span class="text-xs font-medium text-slate-500">业务品类数</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-bold text-slate-900 font-mono tabular-nums">{{ categories.length }}</span>
          <FolderKanban class="h-4 w-4 text-slate-400" />
        </div>
      </div>
    </div>

    <!-- Recent Tasks Table -->
    <div class="enterprise-card overflow-hidden">
      <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3 bg-white">
        <h2 class="text-xs font-bold text-slate-900 uppercase tracking-wider">近期制作任务</h2>
        <RouterLink
          to="/tasks"
          class="text-xs text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
        >
          <span>查看全部</span>
          <ArrowRight class="h-3 w-3" />
        </RouterLink>
      </div>

      <!-- Empty State -->
      <div v-if="!loading && tasks.length === 0" class="py-16 text-center">
        <Film class="mx-auto h-8 w-8 text-slate-300 mb-2" />
        <p class="text-xs text-slate-500 mb-3">暂无制作任务</p>
        <button
          @click="router.push('/tasks?create=true')"
          :disabled="authStore.isDisabling"
          class="btn-primary text-xs"
        >
          <Plus class="h-3.5 w-3.5" />
          <span>新建第一个制作任务</span>
        </button>
      </div>

      <!-- Table View -->
      <div v-else class="overflow-x-auto">
        <table class="w-full border-collapse text-left">
          <thead>
            <tr>
              <th class="enterprise-th">任务名称</th>
              <th class="enterprise-th">业务类型</th>
              <th class="enterprise-th">当前阶段</th>
              <th class="enterprise-th">状态</th>
              <th class="enterprise-th">更新时间</th>
              <th class="enterprise-th text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in tasks.slice(0, 8)"
              :key="task.id"
              class="hover:bg-slate-50 transition-colors"
            >
              <td class="enterprise-td font-medium text-slate-900">
                <RouterLink
                  :to="`/tasks/${task.id}`"
                  class="hover:text-blue-600 transition truncate max-w-xs block"
                  :title="task.name"
                >
                  {{ task.name }}
                </RouterLink>
              </td>
              <td class="enterprise-td">
                <span class="badge-neutral text-[11px]">
                  {{ CONTENT_TYPE_LABELS[task.content_type] || task.content_type }}
                </span>
              </td>
              <td class="enterprise-td text-xs text-slate-600">
                {{ getStageLabel(task.stage) }}
              </td>
              <td class="enterprise-td">
                <span :class="getStatusBadge(task.status).class">
                  {{ getStatusBadge(task.status).label }}
                </span>
              </td>
              <td class="enterprise-td text-xs text-slate-500 font-mono">
                {{ formatDate(task.updated_at) }}
              </td>
              <td class="enterprise-td text-right">
                <RouterLink
                  :to="`/tasks/${task.id}`"
                  class="text-xs text-blue-600 hover:text-blue-700 font-medium"
                >
                  进入工作台
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
