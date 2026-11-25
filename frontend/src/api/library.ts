import { apiClient } from './client'

export type ContentType = 'commerce' | 'brand' | 'knowledge' | 'story'
export type MaterialType = 'research' | 'outline' | 'analysis'

export interface Category {
  id: number
  user_id: number
  name: string
  content_type: ContentType
  sort_order: number
  created_at: string
  updated_at: string
}

export interface Material {
  id: number
  user_id: number
  category_id: number
  title: string
  material_type: MaterialType
  content: string
  sources: Array<{
    title: string
    url?: string
    excerpt?: string
    accessed_at?: string
  }>
  created_at: string
  updated_at: string
}

export interface Asset {
  id: number
  user_id: number
  category_id: number
  name: string
  file_key: string
  mime_type: string
  file_size: number
  created_at: string
  updated_at: string
}

export const libraryApi = {
  listCategories(contentType?: ContentType) {
    const params = contentType ? { content_type: contentType } : {}
    return apiClient.get<{ items: Category[]; total: number }>('/categories', { params })
  },
  createCategory(data: { name: string; content_type: ContentType; sort_order?: number }) {
    return apiClient.post<Category>('/categories', data)
  },
  listMaterials(categoryId: number) {
    return apiClient.get<{ items: Material[]; total: number }>('/materials', {
      params: { category_id: categoryId },
    })
  },
  createMaterial(data: {
    category_id: number
    title: string
    material_type: MaterialType
    content: string
    sources?: any[]
  }) {
    return apiClient.post<Material>('/materials', data)
  },
  listAssets(categoryId: number) {
    return apiClient.get<{ items: Asset[]; total: number }>('/assets', {
      params: { category_id: categoryId },
    })
  },
  uploadAsset(categoryId: number, file: File, name?: string) {
    const formData = new FormData()
    formData.append('category_id', categoryId.toString())
    formData.append('file', file)
    if (name) formData.append('name', name)
    return apiClient.post<Asset>('/assets/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getAssetFileUrl(assetId: number): string {
    return `/api/assets/${assetId}/file`
  },
  async fetchAssetBlobUrl(assetId: number): Promise<string> {
    const res = await apiClient.get(`/assets/${assetId}/file`, { responseType: 'blob' })
    return URL.createObjectURL(res.data)
  },
}
