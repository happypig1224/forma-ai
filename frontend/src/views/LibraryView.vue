<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  Plus,
  Upload,
  FolderPlus,
  Image,
  FileText,
  Search,
  Folder,
  Copy,
  Check,
  X,
  Loader2,
} from 'lucide-vue-next'
import {
  libraryApi,
  type Asset,
  type Category,
  type ContentType,
  type Material,
  type MaterialType,
} from '@/api/library'

const contentTypes: { label: string; value: ContentType }[] = [
  { label: '电商带货', value: 'commerce' },
  { label: '品牌宣传', value: 'brand' },
  { label: '知识干货', value: 'knowledge' },
  { label: '剧情故事', value: 'story' },
]

const activeType = ref<ContentType>('commerce')
const categories = ref<Category[]>([])
const selectedCategoryId = ref<number | null>(null)
const categorySearch = ref('')

const materials = ref<Material[]>([])
const assets = ref<Asset[]>([])
const activeTab = ref<'materials' | 'assets'>('materials')
const materialSearch = ref('')

// Modals
const showNewCategoryModal = ref(false)
const newCategoryName = ref('')
const isCreatingCategory = ref(false)

const showNewMaterialModal = ref(false)
const newMaterialTitle = ref('')
const newMaterialType = ref<MaterialType>('research')
const newMaterialContent = ref('')
const isCreatingMaterial = ref(false)

const copiedMaterialId = ref<number | null>(null)

const currentCategory = computed(() => {
  return categories.value.find((c) => c.id === selectedCategoryId.value)
})

const filteredCategories = computed(() => {
  if (!categorySearch.value.trim()) return categories.value
  const q = categorySearch.value.trim().toLowerCase()
  return categories.value.filter((c) => c.name.toLowerCase().includes(q))
})

const filteredMaterials = computed(() => {
  if (!materialSearch.value.trim()) return materials.value
  const q = materialSearch.value.trim().toLowerCase()
  return materials.value.filter((m) =>
    m.title.toLowerCase().includes(q) || m.content.toLowerCase().includes(q)
  )
})

async function loadCategories() {
  try {
    const res = await libraryApi.listCategories(activeType.value)
    categories.value = res.data.items
    if (categories.value.length > 0) {
      if (!selectedCategoryId.value || !categories.value.some((c) => c.id === selectedCategoryId.value)) {
        selectedCategoryId.value = categories.value[0].id
      }
    } else {
      selectedCategoryId.value = null
    }
  } catch (err) {
    console.error('Failed to load categories', err)
  }
}

async function loadCategoryDetails() {
  if (!selectedCategoryId.value) {
    materials.value = []
    assets.value = []
    return
  }
  try {
    const [matRes, assetRes] = await Promise.all([
      libraryApi.listMaterials(selectedCategoryId.value),
      libraryApi.listAssets(selectedCategoryId.value),
    ])
    materials.value = matRes.data.items
    assets.value = assetRes.data.items
  } catch (err) {
    console.error('Failed to load category details', err)
  }
}

async function copyContent(m: Material) {
  try {
    await navigator.clipboard.writeText(m.content)
    copiedMaterialId.value = m.id
    setTimeout(() => {
      copiedMaterialId.value = null
    }, 2000)
  } catch (err) {
    console.error('Failed to copy', err)
  }
}

watch(activeType, async () => {
  await loadCategories()
  await loadCategoryDetails()
})

watch(selectedCategoryId, async () => {
  await loadCategoryDetails()
})

onMounted(async () => {
  await loadCategories()
  await loadCategoryDetails()
})

async function handleCreateCategory() {
  if (!newCategoryName.value.trim()) return
  isCreatingCategory.value = true
  try {
    const res = await libraryApi.createCategory({
      name: newCategoryName.value.trim(),
      content_type: activeType.value,
    })
    newCategoryName.value = ''
    showNewCategoryModal.value = false
    await loadCategories()
    selectedCategoryId.value = res.data.id
  } catch (err) {
    console.error('Failed to create category', err)
  } finally {
    isCreatingCategory.value = false
  }
}

async function handleCreateMaterial() {
  if (!selectedCategoryId.value || !newMaterialTitle.value.trim() || !newMaterialContent.value.trim()) {
    return
  }
  isCreatingMaterial.value = true
  try {
    await libraryApi.createMaterial({
      category_id: selectedCategoryId.value,
      title: newMaterialTitle.value.trim(),
      material_type: newMaterialType.value,
      content: newMaterialContent.value.trim(),
    })
    newMaterialTitle.value = ''
    newMaterialContent.value = ''
    showNewMaterialModal.value = false
    await loadCategoryDetails()
  } catch (err) {
    console.error('Failed to create material', err)
  } finally {
    isCreatingMaterial.value = false
  }
}

async function handleFileUpload(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files || target.files.length === 0 || !selectedCategoryId.value) return
  const file = target.files[0]
  try {
    await libraryApi.uploadAsset(selectedCategoryId.value, file)
    await loadCategoryDetails()
    target.value = ''
  } catch (err) {
    console.error('Failed to upload asset', err)
  }
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div>
        <h1 class="text-lg font-bold text-slate-900 tracking-tight">素材与分类</h1>
        <p class="text-xs text-slate-500 mt-0.5">业务品类与制作素材资产库</p>
      </div>

      <!-- Type Filter Tabs -->
      <div class="flex items-center gap-1 rounded bg-slate-100 p-1 text-xs font-medium">
        <button
          v-for="item in contentTypes"
          :key="item.value"
          @click="activeType = item.value"
          :class="[
            activeType === item.value
              ? 'bg-white text-slate-900 font-semibold shadow-xs'
              : 'text-slate-600 hover:text-slate-900'
          ]"
          class="rounded px-3 py-1 transition"
        >
          {{ item.label }}
        </button>
      </div>
    </div>

    <!-- Master-Detail Two-Column Layout -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
      <!-- Left Column: Category List (4 cols) -->
      <div class="lg:col-span-4 enterprise-card p-3 space-y-3">
        <div class="flex items-center justify-between border-b border-slate-200 pb-2">
          <div class="flex items-center gap-1.5">
            <span class="text-xs font-bold text-slate-900 uppercase">品类目录</span>
            <span class="badge-neutral text-[10px] font-mono">
              {{ categories.length }}
            </span>
          </div>

          <button
            @click="showNewCategoryModal = true"
            class="btn-primary text-xs !py-1 !px-2"
          >
            <FolderPlus class="h-3 w-3" />
            <span>新建品类</span>
          </button>
        </div>

        <!-- Search input -->
        <div class="relative">
          <Search class="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
          <input
            v-model="categorySearch"
            type="text"
            placeholder="搜索品类..."
            class="enterprise-input pl-8 text-xs !py-1"
          />
        </div>

        <!-- Category list -->
        <div class="space-y-1 max-h-[580px] overflow-y-auto pr-1">
          <div v-if="filteredCategories.length === 0" class="py-10 text-center text-xs text-slate-400">
            暂无品类
          </div>
          <div
            v-for="cat in filteredCategories"
            :key="cat.id"
            @click="selectedCategoryId = cat.id"
            class="flex cursor-pointer items-center justify-between rounded p-2 text-xs transition"
            :class="[
              selectedCategoryId === cat.id
                ? 'bg-blue-50 text-blue-700 font-medium'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            ]"
          >
            <div class="flex items-center gap-2 truncate">
              <Folder class="h-3.5 w-3.5 shrink-0" :class="selectedCategoryId === cat.id ? 'text-blue-600' : 'text-slate-400'" />
              <span class="truncate">{{ cat.name }}</span>
            </div>
            <span class="text-[10px] text-slate-400 font-mono shrink-0">#{{ cat.id }}</span>
          </div>
        </div>
      </div>

      <!-- Right Column: Materials & Assets (8 cols) -->
      <div class="lg:col-span-8 space-y-4">
        <div v-if="selectedCategoryId" class="enterprise-card p-4 space-y-4">
          <!-- Category Header & Tab Switch -->
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-200 pb-3">
            <div>
              <h2 class="text-sm font-bold text-slate-900">{{ currentCategory?.name }}</h2>
              <span class="badge-neutral text-[10px] mt-0.5">
                {{ currentCategory?.content_type }}
              </span>
            </div>

            <div class="flex items-center gap-2">
              <div class="flex rounded bg-slate-100 p-0.5 text-xs">
                <button
                  @click="activeTab = 'materials'"
                  :class="activeTab === 'materials' ? 'bg-white text-slate-900 font-semibold shadow-xs' : 'text-slate-600'"
                  class="rounded px-2.5 py-1 transition flex items-center gap-1"
                >
                  <FileText class="h-3 w-3" />
                  <span>资料 ({{ materials.length }})</span>
                </button>
                <button
                  @click="activeTab = 'assets'"
                  :class="activeTab === 'assets' ? 'bg-white text-slate-900 font-semibold shadow-xs' : 'text-slate-600'"
                  class="rounded px-2.5 py-1 transition flex items-center gap-1"
                >
                  <Image class="h-3 w-3" />
                  <span>资产 ({{ assets.length }})</span>
                </button>
              </div>

              <button
                v-if="activeTab === 'materials'"
                @click="showNewMaterialModal = true"
                class="btn-primary text-xs !py-1 !px-2.5"
              >
                <Plus class="h-3 w-3" />
                <span>录入资料</span>
              </button>

              <label
                v-else
                class="btn-primary text-xs !py-1 !px-2.5 cursor-pointer"
              >
                <Upload class="h-3 w-3" />
                <span>上传素材</span>
                <input
                  type="file"
                  class="hidden"
                  @change="handleFileUpload"
                />
              </label>
            </div>
          </div>

          <!-- Materials Tab -->
          <div v-if="activeTab === 'materials'" class="space-y-3">
            <div class="relative w-full sm:w-60">
              <Search class="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                v-model="materialSearch"
                type="text"
                placeholder="搜索资料..."
                class="enterprise-input pl-8 text-xs !py-1"
              />
            </div>

            <div v-if="filteredMaterials.length === 0" class="py-12 text-center text-xs text-slate-400">
              暂无资料
            </div>

            <div v-else class="space-y-2">
              <div
                v-for="mat in filteredMaterials"
                :key="mat.id"
                class="rounded border border-slate-200 bg-slate-50/50 p-3 space-y-1.5"
              >
                <div class="flex items-center justify-between">
                  <span class="text-xs font-bold text-slate-900">{{ mat.title }}</span>
                  <button
                    @click="copyContent(mat)"
                    class="text-[11px] text-slate-500 hover:text-slate-800 flex items-center gap-1"
                  >
                    <Check v-if="copiedMaterialId === mat.id" class="h-3 w-3 text-emerald-600" />
                    <Copy v-else class="h-3 w-3" />
                    <span>{{ copiedMaterialId === mat.id ? '已复制' : '复制' }}</span>
                  </button>
                </div>
                <p class="text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">{{ mat.content }}</p>
              </div>
            </div>
          </div>

          <!-- Assets Tab -->
          <div v-else class="space-y-3">
            <div v-if="assets.length === 0" class="py-12 text-center text-xs text-slate-400">
              暂无媒体资产文件
            </div>
            <div v-else class="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div
                v-for="ast in assets"
                :key="ast.id"
                class="rounded border border-slate-200 bg-white p-2 text-xs space-y-1"
              >
                <div class="h-20 rounded bg-slate-100 flex items-center justify-center text-slate-400">
                  <Image class="h-6 w-6" />
                </div>
                <div class="truncate font-medium text-slate-800" :title="ast.name">
                  {{ ast.name }}
                </div>
                <div class="text-[10px] text-slate-400 font-mono">
                  {{ (ast.file_size / 1024).toFixed(1) }} KB
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="enterprise-card p-12 text-center text-xs text-slate-400">
          请从左侧选择品类
        </div>
      </div>
    </div>

    <!-- New Category Modal -->
    <div v-if="showNewCategoryModal" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div class="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 space-y-3 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-200 pb-2">
          <h3 class="text-sm font-bold text-slate-900">新建品类</h3>
          <button @click="showNewCategoryModal = false" class="text-slate-400 hover:text-slate-600">
            <X class="h-4 w-4" />
          </button>
        </div>

        <div>
          <label class="block text-xs font-medium text-slate-700 mb-1">品类名称</label>
          <input
            v-model="newCategoryName"
            type="text"
            placeholder="例如：智能穿戴"
            class="enterprise-input text-xs"
            @keydown.enter.prevent="handleCreateCategory"
          />
        </div>

        <div class="flex justify-end gap-2 pt-2 border-t border-slate-200">
          <button
            @click="showNewCategoryModal = false"
            class="btn-secondary text-xs"
          >
            取消
          </button>
          <button
            @click="handleCreateCategory"
            :disabled="isCreatingCategory || !newCategoryName.trim()"
            class="btn-primary text-xs"
          >
            <Loader2 v-if="isCreatingCategory" class="h-3 w-3 animate-spin" />
            <span>创建</span>
          </button>
        </div>
      </div>
    </div>

    <!-- New Material Modal -->
    <div v-if="showNewMaterialModal" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div class="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 space-y-3 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-200 pb-2">
          <h3 class="text-sm font-bold text-slate-900">录入资料素材</h3>
          <button @click="showNewMaterialModal = false" class="text-slate-400 hover:text-slate-600">
            <X class="h-4 w-4" />
          </button>
        </div>

        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-medium text-slate-700 mb-1">资料标题</label>
            <input
              v-model="newMaterialTitle"
              type="text"
              placeholder="例如：核心卖点清单"
              class="enterprise-input text-xs"
            />
          </div>

          <div>
            <label class="block font-medium text-slate-700 mb-1">正文内容</label>
            <textarea
              v-model="newMaterialContent"
              rows="4"
              placeholder="输入素材文案内容..."
              class="enterprise-input text-xs leading-relaxed"
            ></textarea>
          </div>
        </div>

        <div class="flex justify-end gap-2 pt-2 border-t border-slate-200">
          <button
            @click="showNewMaterialModal = false"
            class="btn-secondary text-xs"
          >
            取消
          </button>
          <button
            @click="handleCreateMaterial"
            :disabled="isCreatingMaterial || !newMaterialTitle.trim() || !newMaterialContent.trim()"
            class="btn-primary text-xs"
          >
            <Loader2 v-if="isCreatingMaterial" class="h-3 w-3 animate-spin" />
            <span>保存</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
