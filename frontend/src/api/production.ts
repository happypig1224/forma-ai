import { apiClient } from './client'
import type { ContentType } from './library'

export type TaskStage =
  | 'created'
  | 'research'
  | 'script_proposal'
  | 'script_confirmed'
  | 'keyframes'
  | 'shot_video'
  | 'composition'
  | 'finished'

export type TaskStatus =
  | 'pending'
  | 'running'
  | 'waiting_confirmation'
  | 'paused'
  | 'waiting_retry'
  | 'success'
  | 'failed'

export interface VideoTask {
  id: number
  category_id: number
  name: string
  content_type: ContentType
  stage: TaskStage
  status: TaskStatus
  input_key: string
  checkpoint_key: string | null
  version: number
  created_at: string
  updated_at: string
  finished_at: string | null
  initial_input?: {
    theme?: string
    target_market?: string
    allow_ai_expansion?: boolean
    asset_ids?: number[]
    material_ids?: number[]
  }
}

export interface VideoShot {
  id: number
  task_id: number
  shot_no: number
  title: string
  duration_sec: number
  selected_attempt_id: number | null
  selected_attempt?: VideoAttempt | null
  needs_review: boolean
  version: number
  created_at: string
  updated_at: string
}

export interface VideoAttempt {
  id: number
  task_id: number
  shot_id: number | null
  attempt_no: number
  operation: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  input_key: string
  result_key: string | null
  error_code: string | null
  created_at?: string
}

export interface UsageSummary {
  total_api_calls: number
  total_gpu_seconds: number
  total_storage_bytes: number
  total_cost_cents: number
}

export interface UsageRecord {
  id: number
  task_id: number
  attempt_id: number | null
  metric_type: 'gpu_second' | 'storage_byte' | 'api_call'
  quantity: number
  cost_cents: number
  recorded_at: string
}

export interface DecisionRecord {
  id: number
  task_id: number
  action: string
  target_key: string
  details: Record<string, any>
  idempotency_key: string
  created_at: string
}

export const productionApi = {
  listTasks(categoryId?: number) {
    const params = categoryId ? { category_id: categoryId } : {}
    return apiClient.get<{ items: VideoTask[]; total: number }>('/tasks', { params })
  },
  getTask(taskId: number) {
    return apiClient.get<VideoTask>(`/tasks/${taskId}`)
  },
  createTask(data: {
    category_id: number
    name: string
    content_type: ContentType
    initial_input: {
      theme: string
      target_market?: string
      material_ids?: number[]
      asset_ids?: number[]
      allow_ai_expansion?: boolean
      notes?: string
    }
  }) {
    return apiClient.post<VideoTask>('/tasks', data)
  },
  submitDecision(
    taskId: number,
    data: {
      idempotency_key: string
      expected_version: number
      action: string
      target_key: string
      details: {
        selected_script_index?: number
        spec?: {
          target_duration_sec: number
          aspect_ratio: '9:16' | '16:9'
          resolution: string
          fps: number
        }
        notes?: string
        extra_data?: Record<string, any>
      }
    }
  ) {
    return apiClient.post<DecisionRecord>(`/tasks/${taskId}/decisions`, data)
  },
  listDecisions(taskId: number) {
    return apiClient.get<{ items: DecisionRecord[]; total: number }>(`/tasks/${taskId}/decisions`)
  },
  listShots(taskId: number) {
    return apiClient.get<{ items: VideoShot[]; total: number }>(`/tasks/${taskId}/shots`)
  },
  initializeShots(taskId: number) {
    return apiClient.post<{ items: VideoShot[]; total: number }>(`/tasks/${taskId}/shots/initialize`)
  },
  redoShot(
    taskId: number,
    shotNo: number,
    data: {
      idempotency_key: string
      prompt: string
      notes?: string
      reference_image_key?: string
      negative_prompt?: string
    }
  ) {
    return apiClient.post<VideoAttempt>(`/tasks/${taskId}/shots/${shotNo}/redo`, data)
  },
  selectAttempt(
    taskId: number,
    shotNo: number,
    data: { attempt_id: number; expected_version: number }
  ) {
    return apiClient.post<VideoShot>(`/tasks/${taskId}/shots/${shotNo}/select`, data)
  },
  acceptReview(
    taskId: number,
    shotNo: number,
    data: { expected_version: number }
  ) {
    return apiClient.post<VideoShot>(`/tasks/${taskId}/shots/${shotNo}/accept-review`, data)
  },
  composeTask(taskId: number, data: { expected_version: number; idempotency_key?: string }) {
    const payload = {
      idempotency_key: data.idempotency_key || `compose_${taskId}_${Date.now()}`,
      expected_version: data.expected_version,
    }
    return apiClient.post<VideoTask>(`/tasks/${taskId}/compose`, payload)
  },
  completeTask(taskId: number, data: { expected_version: number; final_video_key: string }) {
    return apiClient.post<VideoTask>(`/tasks/${taskId}/complete`, data)
  },
  downloadVideo(taskId: number) {
    return apiClient.get(`/tasks/${taskId}/download`, { responseType: 'blob' })
  },
  listUsages(taskId: number) {
    return apiClient.get<{
      items: UsageRecord[]
      summary: UsageSummary
      total: number
    }>(`/tasks/${taskId}/usages`)
  },
}
