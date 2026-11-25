<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Plus,
  Film,
  Search,
  X,
  Loader2,
  Image as ImageIcon,
} from 'lucide-vue-next'
import { libraryApi, type Category, type ContentType, type Asset } from '@/api/library'
import { productionApi, type VideoTask } from '@/api/production'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const tasks = ref<VideoTask[]>([])
const categories = ref<Category[]>([])
const loading = ref(true)
const searchQuery = ref('')
const selectedTypeFilter = ref<string>('all')

// Create Modal
const showCreateModal = ref(false)
const taskName = ref('')
const selectedCategoryId = ref<number | null>(null)
const selectedContentType = ref<ContentType>('commerce')
const taskTheme = ref('')
const taskTargetMarket = ref('')
const modalError = ref('')
const isCreating = ref(false)

// Quick add custom category
const showCustomCategoryInput = ref(false)
const customCategoryName = ref('')
const isAddingCategory = ref(false)

// Multimodal Reference Images
const categoryAssets = ref<Asset[]>([])
const selectedAssetIds = ref<number[]>([])

async function loadCategoryAssets(catId: number | null) {
  if (!catId) {
    categoryAssets.value = []
    selectedAssetIds.value = []
    return
  }
  try {
    const res = await libraryApi.listAssets(catId)
    categoryAssets.value = (res.data.items || []).filter((a) =>
      a.mime_type.startsWith('image/') || /\.(jpg|jpeg|png|webp|gif)$/i.test(a.name)
    )
  } catch {
    categoryAssets.value = []
  }
}

function toggleAssetSelection(id: number) {
  if (selectedAssetIds.value.includes(id)) {
    selectedAssetIds.value = selectedAssetIds.value.filter((x) => x !== id)
  } else {
    if (selectedAssetIds.value.length >= 3) {
      selectedAssetIds.value.shift()
    }
    selectedAssetIds.value.push(id)
  }
}

watch(selectedCategoryId, (newId) => {
  loadCategoryAssets(newId)
})

const CONTENT_TYPES_CONFIG: {
  type: ContentType
  title: string
  presets: string[]
}[] = [
  {
    type: 'commerce',
    title: '电商带货',
    presets: [
      '3C数码与智能穿戴',
      '美妆个护与彩妆护肤',
      '服饰鞋包与潮流穿搭',
      '食品饮料与生鲜特产',
      '家居生活与日用百货',
      '运动户外与露营装备',
      '母婴用品与儿童玩具',
      '智能家电与生活电器',
      '宠物用品与健康食品',
      '黄金珠宝与时尚配饰',
    ],
  },
  {
    type: 'brand',
    title: '品牌宣传',
    presets: [
      '品牌愿景与价值主张',
      '新品发布与产品亮相',
      '品牌微电影与温情故事',
      '创始人IP与创业初心',
      '企业实力与供应链展示',
      '客户见证与成功案例',
      '重要展会与峰会营销',
      '公益环保与社会责任',
    ],
  },
  {
    type: 'knowledge',
    title: '知识干货',
    presets: [
      '职场进阶与提效工具',
      '商业财富与模式拆解',
      '前沿科技与数码测评',
      '人文历史与社科通识',
      '健康养生与科学减脂',
      '生活百科与实用避坑',
      '认知升级与情绪疗愈',
      '考公考证与实用语言',
    ],
  },
  {
    type: 'story',
    title: '剧情故事',
    presets: [
      '都市逆袭与热血打脸',
      '甜宠言情与情感纠葛',
      '职场博弈与人性反转',
      '悬疑推理与奇幻脑洞',
      '家庭伦理与年代生活',
      '爆笑日常与沙雕喜剧',
      '古装权谋与快意仙侠',
      '文旅非遗与正向微剧',
    ],
  },
]

const currentTypePresets = computed(() => {
  const cfg = CONTENT_TYPES_CONFIG.find((c) => c.type === selectedContentType.value)
  return cfg ? cfg.presets : []
})

const categoriesForSelectedType = computed(() => {
  return categories.value.filter((c) => c.content_type === selectedContentType.value)
})

const filteredTasks = computed(() => {
  let list = tasks.value
  if (selectedTypeFilter.value !== 'all') {
    list = list.filter((t) => t.content_type === selectedTypeFilter.value)
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter((t) => t.name.toLowerCase().includes(q))
  }
  return list
})

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

function syncSelectedCategory() {
  const forType = categories.value.filter((c) => c.content_type === selectedContentType.value)
  if (forType.length > 0) {
    if (!selectedCategoryId.value || !forType.some((c) => c.id === selectedCategoryId.value)) {
      selectedCategoryId.value = forType[0].id
    }
  } else {
    selectedCategoryId.value = null
  }
}

function handleSelectContentType(type: ContentType) {
  selectedContentType.value = type
  syncSelectedCategory()
}

async function selectOrCreateCategory(name: string) {
  modalError.value = ''
  const existing = categoriesForSelectedType.value.find((c) => c.name === name)
  if (existing) {
    selectedCategoryId.value = existing.id
    return
  }

  isAddingCategory.value = true
  try {
    const res = await libraryApi.createCategory({
      name,
      content_type: selectedContentType.value,
    })
    categories.value.push(res.data)
    selectedCategoryId.value = res.data.id
    customCategoryName.value = ''
    showCustomCategoryInput.value = false
  } catch (err: any) {
    modalError.value = err.response?.data?.detail || '添加品类失败'
  } finally {
    isAddingCategory.value = false
  }
}

async function handleCreateTask() {
  modalError.value = ''
  if (!selectedCategoryId.value) {
    modalError.value = '请选择品类'
    return
  }
  if (!taskName.value.trim() || !taskTheme.value.trim()) {
    modalError.value = '请填写任务名称与制作主题'
    return
  }

  isCreating.value = true
  try {
    const res = await productionApi.createTask({
      category_id: selectedCategoryId.value,
      name: taskName.value.trim(),
      content_type: selectedContentType.value,
      initial_input: {
        theme: taskTheme.value.trim(),
        target_market: taskTargetMarket.value.trim() || undefined,
        asset_ids: selectedAssetIds.value,
        allow_ai_expansion: true,
      },
    })
    showCreateModal.value = false
    router.push(`/tasks/${res.data.id}`)
  } catch (err: any) {
    modalError.value = err.response?.data?.detail || '创建任务失败'
  } finally {
    isCreating.value = false
  }
}

function openCreateModal(presetType?: ContentType) {
  if (presetType) {
    selectedContentType.value = presetType
  }
  taskName.value = ''
  taskTheme.value = ''
  taskTargetMarket.value = ''
  modalError.value = ''
  selectedAssetIds.value = []
  showCustomCategoryInput.value = false
  syncSelectedCategory()
  showCreateModal.value = true
}

async function loadData() {
  loading.value = true
  try {
    const [taskRes, catRes] = await Promise.all([
      productionApi.listTasks(),
      libraryApi.listCategories(),
    ])
    tasks.value = taskRes.data.items
    categories.value = catRes.data.items
    syncSelectedCategory()
  } catch (err) {
    console.error('Failed to load tasks and categories', err)
  } finally {
    loading.value = false
  }
}

watch(
  () => route.query.create,
  (val) => {
    if (val === 'true') {
      openCreateModal()
    }
  },
  { immediate: true }
)

onMounted(loadData)
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div>
        <h1 class="text-lg font-bold text-slate-900 tracking-tight">制作任务</h1>
        <p class="text-xs text-slate-500 mt-0.5">短视频制作任务管理与流程追踪</p>
      </div>

      <button
        @click="openCreateModal()"
        :disabled="authStore.isDisabling"
        class="btn-primary text-xs"
      >
        <Plus class="h-3.5 w-3.5" />
        <span>新建任务</span>
      </button>
    </div>

    <!-- Filter & Search Toolbar -->
    <div class="flex flex-col sm:flex-row items-center justify-between gap-3 enterprise-card p-3">
      <!-- Search Input -->
      <div class="relative w-full sm:w-64">
        <Search class="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索任务名称..."
          class="enterprise-input pl-8 text-xs !py-1.5"
        />
      </div>

      <!-- Type Filter Tabs -->
      <div class="flex items-center gap-1 overflow-x-auto w-full sm:w-auto text-xs">
        <button
          @click="selectedTypeFilter = 'all'"
          :class="selectedTypeFilter === 'all' ? 'bg-slate-900 text-white font-medium' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'"
          class="rounded px-2.5 py-1 text-xs transition"
        >
          全部
        </button>
        <button
          v-for="cfg in CONTENT_TYPES_CONFIG"
          :key="cfg.type"
          @click="selectedTypeFilter = cfg.type"
          :class="selectedTypeFilter === cfg.type ? 'bg-slate-900 text-white font-medium' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'"
          class="rounded px-2.5 py-1 text-xs transition"
        >
          {{ cfg.title }}
        </button>
      </div>
    </div>

    <!-- Tasks Table -->
    <div class="enterprise-card overflow-hidden">
      <!-- Empty State -->
      <div v-if="!loading && filteredTasks.length === 0" class="py-16 text-center">
        <Film class="mx-auto h-8 w-8 text-slate-300 mb-2" />
        <p class="text-xs text-slate-500 mb-3">暂无匹配任务</p>
        <button
          @click="openCreateModal()"
          :disabled="authStore.isDisabling"
          class="btn-primary text-xs"
        >
          <Plus class="h-3.5 w-3.5" />
          <span>新建制作任务</span>
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
              v-for="task in filteredTasks"
              :key="task.id"
              class="hover:bg-slate-50 transition-colors"
            >
              <td class="enterprise-td font-medium text-slate-900">
                <RouterLink
                  :to="`/tasks/${task.id}`"
                  class="hover:text-blue-600 transition truncate max-w-sm block"
                  :title="task.name"
                >
                  {{ task.name }}
                </RouterLink>
              </td>
              <td class="enterprise-td">
                <span class="badge-neutral text-[11px]">
                  {{ CONTENT_TYPES_CONFIG.find(c => c.type === task.content_type)?.title || task.content_type }}
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
                  进入制作台
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Create Task Modal -->
    <div
      v-if="showCreateModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    >
      <div class="w-full max-w-lg rounded-lg border border-slate-200 bg-white p-6 shadow-lg">
        <!-- Modal Header -->
        <div class="flex items-center justify-between border-b border-slate-200 pb-3 mb-4">
          <h2 class="text-sm font-bold text-slate-900">新建制作任务</h2>
          <button
            @click="showCreateModal = false"
            class="text-slate-400 hover:text-slate-600"
          >
            <X class="h-4 w-4" />
          </button>
        </div>

        <!-- Error Banner -->
        <div
          v-if="modalError"
          class="mb-3 rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-700"
        >
          {{ modalError }}
        </div>

        <div class="space-y-4">
          <!-- Business Type Selection -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1.5">业务类型</label>
            <div class="grid grid-cols-4 gap-1.5">
              <button
                v-for="cfg in CONTENT_TYPES_CONFIG"
                :key="cfg.type"
                type="button"
                @click="handleSelectContentType(cfg.type)"
                class="rounded border px-2.5 py-1.5 text-xs text-center transition"
                :class="[
                  selectedContentType === cfg.type
                    ? 'border-blue-600 bg-blue-50 text-blue-700 font-semibold'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                ]"
              >
                {{ cfg.title }}
              </button>
            </div>
          </div>

          <!-- Category Selection -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="text-xs font-medium text-slate-700">所属品类</label>
              <button
                type="button"
                @click="showCustomCategoryInput = !showCustomCategoryInput"
                class="text-[11px] text-blue-600 hover:underline"
              >
                {{ showCustomCategoryInput ? '选择已有品类' : '+ 新增品类' }}
              </button>
            </div>

            <!-- Custom Category Input if active -->
            <div v-if="showCustomCategoryInput" class="flex items-center gap-1.5 mb-2">
              <input
                v-model="customCategoryName"
                type="text"
                placeholder="输入新品类名称..."
                class="enterprise-input text-xs !py-1.5 flex-1"
                @keydown.enter.prevent="selectOrCreateCategory(customCategoryName)"
              />
              <button
                type="button"
                @click="selectOrCreateCategory(customCategoryName)"
                :disabled="isAddingCategory || !customCategoryName.trim()"
                class="btn-primary text-xs !py-1.5"
              >
                添加
              </button>
            </div>

            <!-- Category Select Dropdown -->
            <select
              v-else
              v-model="selectedCategoryId"
              class="enterprise-input text-xs !py-1.5"
            >
              <option :value="null" disabled>请选择所属品类</option>
              <option
                v-for="cat in categoriesForSelectedType"
                :key="cat.id"
                :value="cat.id"
              >
                {{ cat.name }}
              </option>
            </select>

            <!-- Popular Presets -->
            <div class="mt-2 flex flex-wrap gap-1">
              <button
                v-for="preset in currentTypePresets.slice(0, 8)"
                :key="preset"
                type="button"
                @click="selectOrCreateCategory(preset)"
                class="rounded border px-1.5 py-0.5 text-[11px] transition"
                :class="[
                  categoriesForSelectedType.some(c => c.id === selectedCategoryId && c.name === preset)
                    ? 'border-blue-600 bg-blue-50 text-blue-700 font-medium'
                    : 'border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-300'
                ]"
              >
                {{ preset }}
              </button>
            </div>

            <!-- Visual Reference Assets (Multimodal Vision) -->
            <div v-if="categoryAssets.length > 0" class="mt-3 pt-2.5 border-t border-slate-100">
              <div class="flex items-center justify-between mb-1.5">
                <label class="text-xs font-medium text-slate-700">关联参考图（多模态视觉识图，选填）</label>
                <span class="text-[11px] text-slate-400">已选 {{ selectedAssetIds.length }}/3 张</span>
              </div>
              <div class="grid grid-cols-4 gap-2 max-h-36 overflow-y-auto p-1.5 border border-slate-200 rounded-md bg-slate-50">
                <button
                  v-for="ast in categoryAssets"
                  :key="ast.id"
                  type="button"
                  @click="toggleAssetSelection(ast.id)"
                  class="relative flex flex-col items-center p-1.5 rounded border text-left transition bg-white"
                  :class="selectedAssetIds.includes(ast.id) ? 'border-blue-600 ring-1 ring-blue-600 bg-blue-50/30' : 'border-slate-200 hover:border-slate-300'"
                >
                  <div class="h-10 w-full rounded bg-slate-100 flex items-center justify-center overflow-hidden mb-1 text-[10px] text-slate-400">
                    <ImageIcon class="h-4 w-4 text-slate-400" />
                  </div>
                  <span class="text-[11px] text-slate-700 truncate w-full" :title="ast.name">{{ ast.name }}</span>
                  <span v-if="selectedAssetIds.includes(ast.id)" class="absolute top-1 right-1 h-3.5 w-3.5 bg-blue-600 text-white rounded-full flex items-center justify-center text-[9px] font-bold">✓</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Task Name -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">任务名称</label>
            <input
              v-model="taskName"
              type="text"
              placeholder="例如：2026 夏季爆款跑鞋 15 秒促单短视频"
              class="enterprise-input text-xs"
            />
          </div>

          <!-- Video Theme -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">制作主题</label>
            <textarea
              v-model="taskTheme"
              rows="2"
              placeholder="详细阐述视频诉求、主推卖点或剧情走势..."
              class="enterprise-input text-xs leading-relaxed"
            ></textarea>
          </div>

          <!-- Target Market (Optional) -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">目标人群（选填）</label>
            <input
              v-model="taskTargetMarket"
              type="text"
              placeholder="例如：25-35岁都市白领"
              class="enterprise-input text-xs"
            />
          </div>
        </div>

        <!-- Modal Footer -->
        <div class="flex justify-end gap-2 pt-4 border-t border-slate-200 mt-5">
          <button
            type="button"
            @click="showCreateModal = false"
            class="btn-secondary text-xs"
          >
            取消
          </button>
          <button
            type="button"
            @click="handleCreateTask"
            :disabled="isCreating"
            class="btn-primary text-xs"
          >
            <Loader2 v-if="isCreating" class="h-3 w-3 animate-spin" />
            <span>创建任务</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
