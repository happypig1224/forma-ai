<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  RefreshCw,
  Play,
  RotateCcw,
  ArrowLeft,
  ArrowRight,
  Sliders,
  Check,
  Download,
  Loader2,
  X,
  Image as ImageIcon,
} from 'lucide-vue-next'
import {
  productionApi,
  type UsageRecord,
  type UsageSummary,
  type VideoShot,
  type VideoTask,
} from '@/api/production'
import { libraryApi, type Asset } from '@/api/library'

const route = useRoute()
const router = useRouter()
const taskId = Number(route.params.id)

const task = ref<VideoTask | null>(null)
const shots = ref<VideoShot[]>([])
const usages = ref<UsageRecord[]>([])
const usageSummary = ref<UsageSummary | null>(null)
const loading = ref(true)
const isSubmitting = ref(false)

// Redo modal
const showRedoModal = ref(false)
const targetShotNo = ref<number | null>(null)
const promptAdjustment = ref('')
const selectedRefAssetId = ref<number | null>(null)
const customRefImageUrl = ref('')
const refImageSource = ref<'library' | 'custom'>('library')
const categoryAssets = ref<Asset[]>([])
const loadingAssets = ref(false)

const composeError = ref('')
const composeSuccess = ref(false)

// Script Proposals state for Stage 2
const selectedScriptIdx = ref(0)
const selectedAspectRatio = ref<'9:16' | '16:9'>('9:16')
const selectedDuration = ref(15)
const selectedResolution = ref<'1080p' | '4k'>('1080p')

const scriptProposals = computed(() => {
  const theme = task.value?.initial_input?.theme || task.value?.name || '核心主题'
  const targetMarket = task.value?.initial_input?.target_market || '目标受众'
  const contentType = task.value?.content_type || 'commerce'

  if (contentType === 'brand') {
    return [
      {
        title: '方案 A：品牌态度型',
        desc: `以品牌愿景切入，连接【${targetMarket}】，确立行业引领心智。`,
        hook: `“关于【${theme}】，我们有了全新的答案。”`,
        body: `坚持严苛品质与创新美学，与每一位【${targetMarket}】并肩前行。`,
        cta: `“即刻探索全新品牌态度系列，见证品质进阶。”`,
      },
      {
        title: '方案 B：人文共鸣型',
        desc: `挖掘【${targetMarket}】日常生活真实瞬间，让【${theme}】成为温暖陪伴。`,
        hook: `“那些看似微小的坚持，终将在时间的见证下汇聚成光芒。”`,
        body: `融入真实生活场景，以匠心雕琢【${theme}】，守护每一份热爱。`,
        cta: `“点击关注官方品牌动态，开启质感生活。”`,
      },
      {
        title: '方案 C：创新突破型',
        desc: `拆解传统行业瓶颈，公布【${theme}】核心革新与实力底色。`,
        hook: `“当别人还在遵循旧规则，我们已经为【${theme}】制定了新的标杆。”`,
        body: `汇聚科技突破，攻克核心瓶颈，为【${targetMarket}】带来跨代体验。`,
        cta: `“预约新品品鉴会，抢先领略旗舰标杆。”`,
      },
    ]
  }

  if (contentType === 'knowledge') {
    return [
      {
        title: '方案 A：通识避坑型',
        desc: `直击【${targetMarket}】在【${theme}】上的常见误区，拆解底层逻辑。`,
        hook: `“为什么你做【${theme}】总是事倍功半？很可能底层逻辑走偏了！”`,
        body: `提炼3大实操模型，手把手拆解闭环链路，帮助【${targetMarket}】快速上手。`,
        cta: `“点赞收藏干货，持续为你输出系统化指南。”`,
      },
      {
        title: '方案 B：深度拆解型',
        desc: `从原理到打法全景推演，结构化呈现【${theme}】核心脉络。`,
        hook: `“花3分钟弄懂【${theme}】的底层架构，胜过盲目摸索3个月！”`,
        body: `系统梳理行业实践范式，由浅入深层层穿透，构建专业护城河。`,
        cta: `“完整思维导图已整理完毕，评论区即可领取。”`,
      },
      {
        title: '方案 C：前沿洞察型',
        desc: `研判行业风向与拐点，为【${targetMarket}】提供决策参考。`,
        hook: `“关于【${theme}】的这套新打法，将彻底改写很多人的认知习惯！”`,
        body: `结合最新一手数据与风向标，透视【${theme}】确定性增长路径。`,
        cta: `“你更看好哪种趋势？欢迎在评论区留下你的见解。”`,
      },
    ]
  }

  if (contentType === 'story') {
    return [
      {
        title: '方案 A：悬念反转型',
        desc: `开场戏剧冲突，矛盾爆发节点揭开【${theme}】关键伏笔，反转破局。`,
        hook: `“‘你以为凭借【${theme}】就能翻盘？’ 话音刚落，全场都愣住了……”`,
        body: `快节奏博弈推进，【${theme}】成为扭转局势的绝对关键，爽感层层递进。`,
        cta: `“点击关注快速追更，下一集揭晓隐秘真相。”`,
      },
      {
        title: '方案 B：共情治愈型',
        desc: `聚焦【${targetMarket}】日常生活真实瞬间，以【${theme}】串联温情羁绊。`,
        hook: `“在这个忙碌的城市里，总有一个关于【${theme}】的瞬间抚平所有疲惫……”`,
        body: `细腻白描生活烟火气，直击心底柔软处，展现平凡人对美好的守护。`,
        cta: `“如果这个故事触动了你，转给身边在乎的人。”`,
      },
      {
        title: '方案 C：职场博弈型',
        desc: `真实职场场景为舞台，围绕【${theme}】展开交锋，展现专业力。`,
        hook: `“面对领导的犀利发难，他拿出了【${theme}】……”`,
        body: `情商博弈，以理服人，化解危机，传递高段位处事智慧。`,
        cta: `“换作是你你会怎么应对？留下你的破局妙招。”`,
      },
    ]
  }

  // Default: commerce
  return [
    {
      title: '方案 A：痛点反转型',
      desc: `开篇直击【${targetMarket}】痛点，反转抛出【${theme}】核心优势，高效促单。`,
      hook: `“你是不是也在为【${theme}】频频踩坑而苦恼？今天帮你省下学费！”`,
      body: `针对【${targetMarket}】核心诉求打造，品质与性能兼具，实测体验领先。`,
      cta: `“左下角限时现货特惠锁定，前100名再享专享礼赠。”`,
    },
    {
      title: '方案 B：场景沉浸型',
      desc: `日常生活碎片切入，把【${theme}】自然融入高频场景，引发认同。`,
      hook: `“自从入手了这套【${theme}】，每次用起来都觉得省心省力！”`,
      body: `精致细节与人性化设计并存，给【${targetMarket}】带来稳稳的幸福感。`,
      cta: `“戳评论区置顶链接，给生活添一份称心好物。”`,
    },
    {
      title: '方案 C：硬核对比型',
      desc: `量化参数对比，用严谨实测击碎疑虑，建立转化信任。`,
      hook: `“今天我们直接拆解实测【${theme}】，用数据说话！”`,
      body: `各项关键核心指标全面领先，专为注重实效的【${targetMarket}】量身打造。`,
      cta: `“官方正品保障，支持7天无理由退换，立即抢购。”`,
    },
  ]
})

const STAGES = [
  { key: 'created', label: '初始输入' },
  { key: 'research', label: '商业调研' },
  { key: 'script_proposal', label: '方案选择' },
  { key: 'script_confirmed', label: '规格锁定' },
  { key: 'keyframes', label: '分镜规划' },
  { key: 'shot_video', label: '镜头制作' },
  { key: 'composition', label: '成片合成' },
  { key: 'finished', label: '成果归档' },
]

const currentStageIndex = computed(() => {
  if (!task.value) return 0
  const idx = STAGES.findIndex((s) => s.key === task.value?.stage)
  return idx >= 0 ? idx : 0
})

const allShotsReadyForCompose = computed(() => {
  if (shots.value.length === 0) return false
  const unselected = shots.value.some((s) => s.selected_attempt_id === null)
  const pendingReview = shots.value.some((s) => s.needs_review)
  return !unselected && !pendingReview
})

async function loadStudioData() {
  loading.value = true
  try {
    const [taskRes, shotsRes, usageRes] = await Promise.all([
      productionApi.getTask(taskId),
      productionApi.listShots(taskId),
      productionApi.listUsages(taskId),
    ])
    task.value = taskRes.data
    shots.value = shotsRes.data.items
    usages.value = usageRes.data.items
    usageSummary.value = usageRes.data.summary

    if (
      ['keyframes', 'shot_video', 'composition', 'finished'].includes(task.value.stage) &&
      shots.value.length === 0
    ) {
      const initRes = await productionApi.initializeShots(taskId)
      shots.value = initRes.data.items
    }
  } catch (err) {
    console.error('Failed to load task studio details', err)
  } finally {
    loading.value = false
  }
}

async function handleAdvanceToScriptProposal() {
  if (!task.value) return
  isSubmitting.value = true
  try {
    await productionApi.submitDecision(taskId, {
      idempotency_key: `skip_res_${taskId}_${Date.now()}`,
      expected_version: task.value.version,
      action: 'skip_research',
      target_key: task.value.input_key,
      details: { notes: '推进至方案选择' },
    })
    await loadStudioData()
  } catch (err: any) {
    alert(err.response?.data?.detail || '推进失败')
  } finally {
    isSubmitting.value = false
  }
}

async function handleSelectAndLockScript() {
  if (!task.value) return
  isSubmitting.value = true
  try {
    await productionApi.submitDecision(taskId, {
      idempotency_key: `sel_script_${taskId}_${Date.now()}`,
      expected_version: task.value.version,
      action: 'select_script',
      target_key: `script_proposal_${selectedScriptIdx.value}`,
      details: {
        selected_script_index: selectedScriptIdx.value,
        spec: {
          target_duration_sec: selectedDuration.value,
          aspect_ratio: selectedAspectRatio.value,
          resolution: selectedResolution.value,
          fps: 30,
        },
        notes: `选定方案: ${scriptProposals.value[selectedScriptIdx.value].title}`,
      },
    })
    await loadStudioData()
  } catch (err: any) {
    alert(err.response?.data?.detail || '锁定方案与规格失败')
  } finally {
    isSubmitting.value = false
  }
}

async function handleConfirmStoryboardAndKeyframes() {
  if (!task.value) return
  isSubmitting.value = true
  try {
    if (task.value.stage === 'script_confirmed') {
      await productionApi.submitDecision(taskId, {
        idempotency_key: `conf_sb_${taskId}_${Date.now()}`,
        expected_version: task.value.version,
        action: 'confirm_storyboard',
        target_key: 'storyboard_layout_v1',
        details: { notes: '确认分镜结构' },
      })
      await loadStudioData()
    }

    await productionApi.initializeShots(taskId)

    if (task.value && task.value.stage === 'keyframes') {
      await productionApi.submitDecision(taskId, {
        idempotency_key: `conf_kf_${taskId}_${Date.now()}`,
        expected_version: task.value.version,
        action: 'confirm_keyframes',
        target_key: 'keyframes_snapshot_v1',
        details: { notes: '确认关键帧方案' },
      })
      await loadStudioData()
    }
  } catch (err: any) {
    alert(err.response?.data?.detail || '推进至镜头制作阶段失败')
  } finally {
    isSubmitting.value = false
  }
}

async function handleAcceptReview(shotNo: number, shotVersion: number) {
  try {
    await productionApi.acceptReview(taskId, shotNo, { expected_version: shotVersion })
    await loadStudioData()
  } catch (err: any) {
    alert(err.response?.data?.detail || '确认复核失败')
  }
}

async function triggerRedo(shotNo: number) {
  targetShotNo.value = shotNo
  promptAdjustment.value = ''
  selectedRefAssetId.value = null
  customRefImageUrl.value = ''
  refImageSource.value = 'library'
  showRedoModal.value = true

  if (task.value?.category_id && categoryAssets.value.length === 0) {
    loadingAssets.value = true
    try {
      const res = await libraryApi.listAssets(task.value.category_id)
      categoryAssets.value = (res.data.items || []).filter((a) =>
        a.mime_type.startsWith('image/') || /\.(jpg|jpeg|png|webp|gif)$/i.test(a.name)
      )
    } catch {
      categoryAssets.value = []
    } finally {
      loadingAssets.value = false
    }
  }
}

async function handleConfirmRedo() {
  if (!targetShotNo.value) return
  isSubmitting.value = true
  try {
    let refKey: string | undefined = undefined
    if (refImageSource.value === 'library' && selectedRefAssetId.value) {
      const found = categoryAssets.value.find((a) => a.id === selectedRefAssetId.value)
      if (found) refKey = found.file_key
    } else if (refImageSource.value === 'custom' && customRefImageUrl.value.trim()) {
      refKey = customRefImageUrl.value.trim()
    }

    await productionApi.redoShot(taskId, targetShotNo.value, {
      idempotency_key: `redo_${taskId}_${targetShotNo.value}_${Date.now()}`,
      prompt: promptAdjustment.value.trim() || '镜头推进节奏加快，光线更加通透微距',
      reference_image_key: refKey,
    })
    showRedoModal.value = false
    await loadStudioData()
  } catch (err: any) {
    alert(err.response?.data?.detail || '重做派发失败')
  } finally {
    isSubmitting.value = false
  }
}

async function handleCompose() {
  if (!task.value) return
  composeError.value = ''
  composeSuccess.value = false
  isSubmitting.value = true
  try {
    if (task.value.stage === 'shot_video') {
      await productionApi.submitDecision(taskId, {
        idempotency_key: `trig_comp_${taskId}_${Date.now()}`,
        expected_version: task.value.version,
        action: 'trigger_composition',
        target_key: 'composition_manifest',
        details: { notes: '触发成片合成' },
      })
      await loadStudioData()
    }

    if (task.value && task.value.stage === 'composition') {
      await productionApi.composeTask(taskId, { expected_version: task.value.version })
      composeSuccess.value = true
      await loadStudioData()

      if (task.value && task.value.status === 'running') {
        await productionApi.completeTask(taskId, {
          expected_version: task.value.version,
          final_video_key: `final_render_${taskId}.mp4`,
        })
        await loadStudioData()
      }
    }
  } catch (err: any) {
    composeError.value = err.response?.data?.detail || '成片门禁合成未通过，请检查分镜状态'
  } finally {
    isSubmitting.value = false
  }
}

async function handleDownloadVideo() {
  if (!task.value || task.value.stage !== 'finished') {
    alert('成片尚未合成归档，暂不可下载')
    return
  }
  try {
    const res = await productionApi.downloadVideo(taskId)
    const blob = new Blob([res.data], { type: 'video/mp4' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `formaai_task_${taskId}_1080p.mp4`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  } catch (err: any) {
    alert(err.response?.data?.detail || '成片下载请求异常')
  }
}

onMounted(loadStudioData)
</script>

<template>
  <div v-if="loading" class="py-24 text-center text-slate-500 text-xs">
    正在加载制作任务...
  </div>

  <div v-else-if="!task" class="py-24 text-center text-slate-500">
    <p class="text-sm font-semibold text-slate-800">未找到指定的制作任务</p>
    <button @click="router.push('/tasks')" class="btn-primary text-xs mt-3">
      返回任务列表
    </button>
  </div>

  <div v-else class="space-y-4">
    <!-- Top Bar: Task Name & Actions -->
    <div class="enterprise-card p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <button
          @click="router.push('/tasks')"
          class="btn-secondary text-xs !py-1 !px-2"
          title="返回任务空间"
        >
          <ArrowLeft class="h-3.5 w-3.5" />
        </button>

        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base font-bold text-slate-900 tracking-tight">{{ task.name }}</h1>
            <span class="badge-neutral text-[10px] uppercase font-mono">
              {{ task.content_type }}
            </span>
            <span class="badge-neutral text-[10px] font-mono">
              v{{ task.version }}
            </span>
            <span
              v-if="task.initial_input?.asset_ids && task.initial_input.asset_ids.length > 0"
              class="badge-neutral text-[10px] font-mono text-blue-700 bg-blue-50 border border-blue-200"
            >
              📸 {{ task.initial_input.asset_ids.length }} 张视觉参考
            </span>
          </div>
          <div class="flex items-center gap-2 text-xs text-slate-500 mt-0.5">
            <span>ID: #{{ task.id }}</span>
            <span>·</span>
            <span class="font-medium" :class="{
              'text-emerald-600': task.status === 'success',
              'text-blue-600': task.status === 'running',
              'text-amber-600': task.status === 'waiting_confirmation',
              'text-slate-500': task.status === 'pending'
            }">
              {{ task.status }}
            </span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <button
          @click="loadStudioData"
          class="btn-secondary text-xs"
        >
          <RefreshCw class="h-3.5 w-3.5" />
          <span>刷新</span>
        </button>
      </div>
    </div>

    <!-- Stepper Navigation -->
    <div class="enterprise-card p-4 overflow-x-auto">
      <div class="flex items-center justify-between min-w-[680px]">
        <div
          v-for="(st, idx) in STAGES"
          :key="st.key"
          class="flex flex-col items-center flex-1 text-center relative"
        >
          <div
            class="flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold border transition"
            :class="[
              idx < currentStageIndex
                ? 'bg-blue-600 text-white border-blue-600'
                : idx === currentStageIndex
                ? 'bg-white text-blue-600 border-blue-600 ring-2 ring-blue-100 font-bold'
                : 'bg-slate-100 text-slate-400 border-slate-200'
            ]"
          >
            <Check v-if="idx < currentStageIndex" class="h-3 w-3" />
            <span v-else>{{ idx + 1 }}</span>
          </div>
          <span
            class="mt-1.5 text-xs whitespace-nowrap"
            :class="idx <= currentStageIndex ? 'font-medium text-slate-900' : 'text-slate-400'"
          >
            {{ st.label }}
          </span>
        </div>
      </div>
    </div>

    <!-- Feedback Banners -->
    <div
      v-if="composeError"
      class="rounded border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 flex items-center justify-between gap-2"
    >
      <span>{{ composeError }}</span>
      <button @click="composeError = ''" class="text-rose-500 hover:text-rose-700">✕</button>
    </div>

    <!-- STAGE 1: Created / Research -->
    <div v-if="task.stage === 'created' || task.stage === 'research'" class="enterprise-card p-5 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-200 pb-3">
        <div>
          <h2 class="text-sm font-bold text-slate-900">选题与诉求确认</h2>
          <p class="text-xs text-slate-500 mt-0.5">确认制作诉求后推进至方案策划</p>
        </div>
        <button
          @click="handleAdvanceToScriptProposal"
          :disabled="isSubmitting"
          class="btn-primary text-xs"
        >
          <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
          <span>生成策划方案</span>
          <ArrowRight class="h-3.5 w-3.5" />
        </button>
      </div>

      <div class="space-y-3 text-xs">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="rounded border border-slate-200 bg-slate-50 p-3">
            <span class="font-medium text-slate-500 block mb-1">制作主题</span>
            <p class="text-slate-800 leading-relaxed">{{ task.initial_input?.theme || '-' }}</p>
          </div>
          <div class="rounded border border-slate-200 bg-slate-50 p-3">
            <span class="font-medium text-slate-500 block mb-1">目标人群</span>
            <p class="text-slate-800 leading-relaxed">{{ task.initial_input?.target_market || '通用人群' }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- STAGE 2: Script Proposal / Script Confirmed -->
    <div v-else-if="task.stage === 'script_proposal' || task.stage === 'script_confirmed'" class="enterprise-card p-5 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-200 pb-3">
        <div>
          <h2 class="text-sm font-bold text-slate-900">方案选择与规格锁定</h2>
          <p class="text-xs text-slate-500 mt-0.5">选择一套策划方案并确定视频输出规格</p>
        </div>

        <button
          v-if="task.stage === 'script_proposal'"
          @click="handleSelectAndLockScript"
          :disabled="isSubmitting"
          class="btn-primary text-xs"
        >
          <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
          <Check class="h-3.5 w-3.5" />
          <span>选定方案并锁定规格</span>
        </button>

        <button
          v-else
          @click="handleConfirmStoryboardAndKeyframes"
          :disabled="isSubmitting"
          class="btn-primary text-xs"
        >
          <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
          <span>确认分镜并生成关键帧</span>
          <ArrowRight class="h-3.5 w-3.5" />
        </button>
      </div>

      <!-- 3 Script Proposal Cards -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div
          v-for="(prop, pIdx) in scriptProposals"
          :key="prop.title"
          @click="selectedScriptIdx = pIdx"
          class="rounded-lg border p-4 cursor-pointer transition flex flex-col justify-between"
          :class="[
            selectedScriptIdx === pIdx
              ? 'border-blue-600 bg-blue-50/20 ring-1 ring-blue-600 shadow-xs'
              : 'border-slate-200 bg-white hover:border-slate-300'
          ]"
        >
          <div class="space-y-2.5">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-900">{{ prop.title }}</span>
              <span
                class="flex h-5 w-5 items-center justify-center rounded-full border text-xs font-semibold"
                :class="selectedScriptIdx === pIdx ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 text-slate-400'"
              >
                {{ String.fromCharCode(65 + pIdx) }}
              </span>
            </div>
            <p class="text-xs text-slate-500">{{ prop.desc }}</p>

            <div class="space-y-1.5 pt-2 border-t border-slate-100 text-xs text-slate-700">
              <p><span class="font-medium text-slate-900">【起幅钩子】:</span> {{ prop.hook }}</p>
              <p><span class="font-medium text-slate-900">【卖点展开】:</span> {{ prop.body }}</p>
              <p><span class="font-medium text-slate-900">【促单指令】:</span> {{ prop.cta }}</p>
            </div>
          </div>

          <div class="mt-3 pt-2.5 border-t border-slate-100 text-xs">
            <span :class="selectedScriptIdx === pIdx ? 'text-blue-600 font-semibold' : 'text-slate-400'">
              {{ selectedScriptIdx === pIdx ? '✓ 已选中' : '点击选择' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Output Specs Configuration -->
      <div class="rounded-lg bg-slate-50 border border-slate-200 p-4 space-y-3">
        <h3 class="text-xs font-bold text-slate-900 flex items-center gap-1.5">
          <Sliders class="h-3.5 w-3.5 text-slate-500" />
          <span>输出视频规格</span>
        </h3>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <!-- Aspect Ratio -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">画幅比例</label>
            <div class="grid grid-cols-2 gap-1.5">
              <button
                type="button"
                @click="selectedAspectRatio = '9:16'"
                class="rounded border py-1.5 text-xs font-medium transition"
                :class="selectedAspectRatio === '9:16' ? 'bg-white border-blue-600 text-blue-700 font-semibold' : 'bg-white border-slate-200 text-slate-600'"
              >
                9:16 竖屏
              </button>
              <button
                type="button"
                @click="selectedAspectRatio = '16:9'"
                class="rounded border py-1.5 text-xs font-medium transition"
                :class="selectedAspectRatio === '16:9' ? 'bg-white border-blue-600 text-blue-700 font-semibold' : 'bg-white border-slate-200 text-slate-600'"
              >
                16:9 横屏
              </button>
            </div>
          </div>

          <!-- Target Duration -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">成片时长</label>
            <select v-model="selectedDuration" class="enterprise-input text-xs !py-1.5">
              <option :value="15">15 秒 (短促单)</option>
              <option :value="30">30 秒 (标准推荐)</option>
              <option :value="60">60 秒 (长评测)</option>
            </select>
          </div>

          <!-- Resolution -->
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">分辨率与帧率</label>
            <select v-model="selectedResolution" class="enterprise-input text-xs !py-1.5">
              <option value="1080p">1080P · 30 FPS</option>
              <option value="4k">4K · 60 FPS</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- STAGE 3: Keyframes -->
    <div v-else-if="task.stage === 'keyframes'" class="enterprise-card p-5 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-200 pb-3">
        <div>
          <h2 class="text-sm font-bold text-slate-900">分镜与关键帧规划</h2>
          <p class="text-xs text-slate-500 mt-0.5">确认各分镜镜头结构与衔接</p>
        </div>

        <button
          @click="handleConfirmStoryboardAndKeyframes"
          :disabled="isSubmitting"
          class="btn-primary text-xs"
        >
          <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
          <Play class="h-3.5 w-3.5 fill-current" />
          <span>确认分镜并生成镜头视频</span>
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div
          v-for="sh in shots"
          :key="sh.id"
          class="rounded-lg border border-slate-200 bg-white p-3 space-y-2"
        >
          <div class="flex items-center justify-between text-xs">
            <span class="font-bold text-slate-800 font-mono">Shot #{{ sh.shot_no }}</span>
            <span class="text-slate-500 font-mono">{{ sh.duration_sec }}s</span>
          </div>
          <div class="h-20 rounded bg-slate-100 flex items-center justify-center text-slate-400 text-xs">
            {{ sh.title }}
          </div>
        </div>
      </div>
    </div>

    <!-- STAGE 4: Shots Pipeline & Multi-Attempt Redo -->
    <div v-else-if="task.stage === 'shot_video'" class="grid grid-cols-1 gap-4 lg:grid-cols-12 items-start">
      <!-- Left: Shots Pipeline (8 cols) -->
      <div class="lg:col-span-8 enterprise-card p-4 space-y-3">
        <div class="flex items-center justify-between border-b border-slate-200 pb-3">
          <div>
            <h2 class="text-sm font-bold text-slate-900">分镜制作列表 ({{ shots.length }} 镜)</h2>
            <p class="text-xs text-slate-500 mt-0.5">支持单镜独立重做与复核确认</p>
          </div>

          <button
            @click="handleCompose"
            :disabled="!allShotsReadyForCompose || isSubmitting"
            class="btn-primary text-xs"
          >
            <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
            <Play class="h-3.5 w-3.5 fill-current" />
            <span>成片门禁合成</span>
          </button>
        </div>

        <div class="space-y-2.5">
          <div
            v-for="shot in shots"
            :key="shot.id"
            class="rounded-lg border p-3 transition"
            :class="[
              shot.needs_review
                ? 'border-amber-300 bg-amber-50/20'
                : 'border-slate-200 bg-white'
            ]"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <div class="flex items-center gap-2">
                  <span class="font-mono text-xs font-bold text-slate-900">Shot #{{ shot.shot_no }}</span>
                  <span class="text-xs text-slate-700 font-medium">{{ shot.title }}</span>
                  <span class="text-xs text-slate-400 font-mono">({{ shot.duration_sec }}s)</span>
                </div>
              </div>

              <div v-if="shot.needs_review" class="badge-warning text-[10px]">
                <AlertTriangle class="h-3 w-3 shrink-0" />
                <span>前序镜头已重做，待确认</span>
              </div>
            </div>

            <div class="mt-3 pt-2 border-t border-slate-100 flex items-center justify-between text-xs">
              <div class="text-slate-500">
                <span>选定制品: </span>
                <span v-if="shot.selected_attempt_id" class="font-mono font-medium text-slate-800">
                  Attempt #{{ shot.selected_attempt_id }}
                </span>
                <span v-else class="text-rose-600 font-medium">未选定</span>
              </div>

              <div class="flex items-center gap-2">
                <button
                  v-if="shot.needs_review"
                  @click="handleAcceptReview(shot.shot_no, shot.version)"
                  class="btn-primary text-xs !py-1 !px-2 !bg-amber-600 hover:!bg-amber-700"
                >
                  确认复核
                </button>
                <button
                  @click="triggerRedo(shot.shot_no)"
                  class="btn-secondary text-xs !py-1 !px-2"
                >
                  <RotateCcw class="h-3 w-3 text-slate-500" />
                  <span>单镜重做</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Right: Real Resource Metrics (4 cols) -->
      <div class="lg:col-span-4 enterprise-card p-4 space-y-3">
        <h2 class="text-xs font-bold text-slate-900 uppercase tracking-wider border-b border-slate-200 pb-2">
          算力与存储审计
        </h2>

        <div v-if="usageSummary" class="space-y-2.5">
          <div class="rounded border border-slate-200 bg-slate-50 p-2.5">
            <span class="text-[11px] text-slate-500 font-medium">GPU 耗时</span>
            <div class="mt-0.5 text-lg font-bold text-slate-900 font-mono">
              {{ usageSummary.total_gpu_seconds.toFixed(1) }} <span class="text-xs font-normal text-slate-500">秒</span>
            </div>
          </div>

          <div class="rounded border border-slate-200 bg-slate-50 p-2.5">
            <span class="text-[11px] text-slate-500 font-medium">存储占用</span>
            <div class="mt-0.5 text-lg font-bold text-slate-900 font-mono">
              {{ (usageSummary.total_storage_bytes / (1024 * 1024)).toFixed(2) }} <span class="text-xs font-normal text-slate-500">MB</span>
            </div>
          </div>

          <div class="rounded border border-slate-200 bg-slate-50 p-2.5">
            <span class="text-[11px] text-slate-500 font-medium">费用累计</span>
            <div class="mt-0.5 text-lg font-bold text-blue-600 font-mono">
              ¥{{ (usageSummary.total_cost_cents / 100).toFixed(2) }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- STAGE 5: Composition & Delivery -->
    <div v-else-if="task.stage === 'composition' || task.stage === 'finished'" class="enterprise-card p-5 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-200 pb-3">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-sm font-bold text-slate-900">成片归档交付</h2>
            <span class="badge-success text-[10px]">
              {{ task.stage === 'finished' ? '制作完成' : '合成中' }}
            </span>
          </div>
          <p class="text-xs text-slate-500 mt-0.5">支持在线预览与原画质文件下载</p>
        </div>

        <div class="flex items-center gap-2">
          <button
            @click="loadStudioData"
            class="btn-secondary text-xs"
          >
            <RefreshCw class="h-3.5 w-3.5" />
            <span>刷新</span>
          </button>
          <button
            type="button"
            @click="handleDownloadVideo"
            class="btn-primary text-xs"
          >
            <Download class="h-3.5 w-3.5" />
            <span>下载成片 (1080P MP4)</span>
          </button>
        </div>
      </div>

      <!-- Video Preview Card -->
      <div class="grid grid-cols-1 md:grid-cols-12 gap-4 items-center">
        <div class="md:col-span-7 rounded-lg bg-slate-900 p-8 aspect-video flex flex-col items-center justify-center text-center">
          <div class="flex h-12 w-12 items-center justify-center rounded-full bg-blue-600 text-white shadow hover:scale-105 transition cursor-pointer mb-2">
            <Play class="h-6 w-6 fill-current translate-x-0.5" />
          </div>
          <div class="text-xs font-medium text-white">{{ task.name }}</div>
          <div class="text-[11px] text-slate-400 mt-0.5">1080x1920 · 30 FPS</div>
        </div>

        <div class="md:col-span-5 space-y-3">
          <div class="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-1.5 text-xs">
            <span class="font-bold text-slate-800">技术规格</span>
            <div class="space-y-1 text-slate-600 font-mono text-[11px]">
              <p>制品识别码：{{ task.checkpoint_key || 'final_video_master.mp4' }}</p>
              <p>视频画幅：9:16 竖屏</p>
              <p>版本快照：v{{ task.version }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Redo Modal -->
    <div v-if="showRedoModal" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div class="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 space-y-3.5 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-200 pb-2">
          <h3 class="text-sm font-bold text-slate-900">单镜重做 (Shot #{{ targetShotNo }})</h3>
          <button @click="showRedoModal = false" class="text-slate-400 hover:text-slate-600">
            <X class="h-4 w-4" />
          </button>
        </div>

        <div>
          <label class="block text-xs font-medium text-slate-700 mb-1">调整提示词</label>
          <textarea
            v-model="promptAdjustment"
            rows="3"
            placeholder="例如：加快运镜推进速度，背景虚化程度加深..."
            class="enterprise-input text-xs leading-relaxed"
          ></textarea>
        </div>

        <!-- Reference Image Selector (I2V) -->
        <div class="pt-1 border-t border-slate-100">
          <div class="flex items-center justify-between mb-1.5">
            <label class="text-xs font-medium text-slate-700">参考图（I2V 图生视频，可选）</label>
            <div class="flex items-center gap-2 text-[11px]">
              <button
                type="button"
                @click="refImageSource = 'library'"
                :class="refImageSource === 'library' ? 'text-blue-600 font-semibold' : 'text-slate-400 hover:text-slate-600'"
              >
                品类素材库
              </button>
              <span class="text-slate-300">|</span>
              <button
                type="button"
                @click="refImageSource = 'custom'"
                :class="refImageSource === 'custom' ? 'text-blue-600 font-semibold' : 'text-slate-400 hover:text-slate-600'"
              >
                外部图片
              </button>
            </div>
          </div>

          <!-- Source: Category Assets -->
          <div v-if="refImageSource === 'library'">
            <div v-if="loadingAssets" class="py-4 text-center text-xs text-slate-400">
              <Loader2 class="h-4 w-4 animate-spin mx-auto mb-1 text-slate-400" />
              <span>加载素材库中...</span>
            </div>
            <div v-else-if="categoryAssets.length === 0" class="p-3 rounded border border-dashed border-slate-200 text-center text-[11px] text-slate-400">
              当前品类暂无素材图片，可切换至【外部图片】输入图片链接
            </div>
            <div v-else class="space-y-1.5">
              <div class="grid grid-cols-4 gap-1.5 max-h-32 overflow-y-auto p-1.5 border border-slate-200 rounded-md bg-slate-50">
                <button
                  v-for="ast in categoryAssets"
                  :key="ast.id"
                  type="button"
                  @click="selectedRefAssetId = selectedRefAssetId === ast.id ? null : ast.id"
                  class="relative flex flex-col items-center p-1 rounded border text-left transition bg-white"
                  :class="selectedRefAssetId === ast.id ? 'border-blue-600 ring-1 ring-blue-600 bg-blue-50/30' : 'border-slate-200 hover:border-slate-300'"
                >
                  <div class="h-9 w-full rounded bg-slate-100 flex items-center justify-center overflow-hidden mb-1 text-[10px] text-slate-400">
                    <ImageIcon class="h-3.5 w-3.5 text-slate-400" />
                  </div>
                  <span class="text-[10px] text-slate-700 truncate w-full" :title="ast.name">{{ ast.name }}</span>
                  <span v-if="selectedRefAssetId === ast.id" class="absolute top-0.5 right-0.5 h-3.5 w-3.5 bg-blue-600 text-white rounded-full flex items-center justify-center text-[9px] font-bold">✓</span>
                </button>
              </div>
              <div v-if="selectedRefAssetId" class="flex items-center justify-between text-[11px] text-blue-600">
                <span>已选中: {{ categoryAssets.find(a => a.id === selectedRefAssetId)?.name }}</span>
                <button type="button" @click="selectedRefAssetId = null" class="text-slate-400 hover:text-slate-600">清除选择</button>
              </div>
            </div>
          </div>

          <!-- Source: Custom URL -->
          <div v-else class="space-y-1">
            <input
              v-model="customRefImageUrl"
              type="text"
              placeholder="https://... 或 data:image/..."
              class="enterprise-input text-xs !py-1.5"
            />
            <p class="text-[10px] text-slate-400">支持输入公开图片 URL 或 Base64 格式，作为首帧图生视频垫图。</p>
          </div>
        </div>

        <div class="flex justify-end gap-2 pt-2 border-t border-slate-200">
          <button
            @click="showRedoModal = false"
            class="btn-secondary text-xs"
          >
            取消
          </button>
          <button
            @click="handleConfirmRedo"
            :disabled="isSubmitting"
            class="btn-primary text-xs"
          >
            <Loader2 v-if="isSubmitting" class="h-3 w-3 animate-spin" />
            <span>确定重做</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
