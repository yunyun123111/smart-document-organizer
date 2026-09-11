import http from './http'

// ---------- 待审核指纹聚类（智能批处理） ----------
export interface ReviewClusterDoc {
  id: number
  filename: string
  document_type: string | null
  created_at: string | null
  confidence: number | null
  suggested_category: string | null
  file_size: number
}

export interface ReviewCluster {
  cluster_id: string
  representative_doc: ReviewClusterDoc
  count: number
  documents: ReviewClusterDoc[]
}

export interface BatchConfirmClusterResult {
  total: number
  success_count: number
  failed_count: number
  results: { id: number; success: boolean; error?: string; path?: string }[]
}

export function getReviewClusters(): Promise<ReviewCluster[]> {
  return http.get('/review/clusters')
}

export function batchConfirmCluster(data: {
  base_document_id: number
  target_document_ids: number[]
  category_path?: string
  save_as_sample?: boolean
}): Promise<{ code: number; message: string; data: BatchConfirmClusterResult }> {
  return http.post('/review/batch-confirm-cluster', data)
}
