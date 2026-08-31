import http from './http'

// ---------- 系统 / Dashboard ----------
export interface DashboardStats {
  total_documents: number
  total_archived: number
  pending_review: number
  today_total: number
  today_archived: number
  running_job: number | null
  document_root: string
  inbox_root: string
  ai_enabled: boolean
  ocr_enabled: boolean
  type_counts: { type: string; count: number }[]
}

export function getDashboardStats(): Promise<DashboardStats> {
  return http.get('/system/dashboard/stats')
}

// ---------- 批量整理 ----------
export interface ProcessingJob {
  id: number
  status: string
  total_files: number
  processed_files: number
  success_count: number
  review_count: number
  failed_count: number
  duplicate_count: number
  created_at?: string
  started_at?: string
  finished_at?: string
}

export function startProcessing(sourceDir?: string): Promise<ProcessingJob> {
  return http.post('/processing/start', { source_dir: sourceDir ?? null })
}

export function getJob(jobId: number): Promise<ProcessingJob> {
  return http.get(`/processing/${jobId}`)
}

export function cancelJob(jobId: number): Promise<{ ok: boolean }> {
  return http.post(`/processing/${jobId}/cancel`)
}

// ---------- 文档库 ----------
export interface DocumentListItem {
  id: number
  original_filename: string
  current_filename: string
  file_type: string
  file_size: number
  document_type: string
  title: string
  confidence: number | null
  status: string
  created_at?: string
  updated_at?: string
}

export interface DocumentField {
  id: number
  field_name: string
  field_value: string
  confidence: number
  source: string
}

export interface DocumentDetail extends DocumentListItem {
  original_path: string
  current_path: string
  mime_type: string
  file_hash: string
  extracted_text: string
  processed_at?: string
  fields: DocumentField[]
}

export function listDocuments(params: {
  status?: string
  document_type?: string
  category?: string
  keyword?: string
  skip?: number
  limit?: number
}): Promise<DocumentListItem[]> {
  return http.get('/documents', { params })
}

export function listDocumentTypes(): Promise<string[]> {
  return http.get('/documents/types')
}

export function listDocumentCategories(): Promise<string[]> {
  return http.get('/documents/categories')
}

export function getDocument(id: number): Promise<DocumentDetail> {
  return http.get(`/documents/${id}`)
}

export function documentFileUrl(id: number): string {
  return `/api/documents/${id}/file`
}

export function deleteDocument(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/documents/${id}`)
}

export function renameDocument(
  id: number,
  filename: string,
): Promise<{ ok: boolean; filename: string; changed: boolean }> {
  return http.post(`/documents/${id}/rename`, { filename })
}

export function batchDeleteDocuments(
  doc_ids: number[],
): Promise<{ ok: boolean; deleted_count: number; missing_count: number }> {
  return http.post('/documents/batch-delete', { doc_ids })
}

export async function exportDocumentsZip(
  doc_ids: number[],
  zipName = 'documents',
): Promise<number> {
  const blob: Blob = await http.post(
    '/documents/export-zip',
    { doc_ids, zip_name: zipName },
    { responseType: 'blob' },
  )
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${zipName}.zip`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  return doc_ids.length
}

export interface UploadResponse {
  document_id: number
  filename: string
  status: string
}

export function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return http.post('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// ---------- 人工审核 ----------
export interface ReviewDetail {
  document: DocumentDetail
  fields: Record<string, string>
  suggested_category: string
  suggested_filename: string
  categories: string[]
  templates: { default: string; category: string }
}

export function listReview(): Promise<DocumentListItem[]> {
  return http.get('/review')
}

export function getReviewDetail(id: number): Promise<ReviewDetail> {
  return http.get(`/review/${id}`)
}

export function updateReview(id: number, data: { document_type?: string; fields?: Record<string, string> }): Promise<{ ok: boolean }> {
  return http.put(`/review/${id}`, data)
}

export function approveReview(
  id: number,
  data: { document_type?: string; category_path?: string; filename?: string; fields?: Record<string, string> },
): Promise<{ ok: boolean; path?: string }> {
  return http.post(`/review/${id}/approve`, data)
}

export function skipReview(id: number): Promise<{ ok: boolean }> {
  return http.post(`/review/${id}/skip`)
}

export interface RecognitionTemplate {
  id: number
  document_type: string
  category_path: string
  require_fields: string[]
  usage_count: number
  enabled: boolean
  note?: string | null
  created_at?: string | null
}

export function listRecognitionTemplates(): Promise<RecognitionTemplate[]> {
  return http.get('/review/templates')
}

export function updateRecognitionTemplate(
  id: number,
  data: { enabled?: boolean; note?: string },
): Promise<{ ok: boolean; enabled: boolean }> {
  return http.put(`/review/templates/${id}`, data)
}

export function deleteRecognitionTemplate(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/review/templates/${id}`)
}

export function batchApprove(
  doc_ids: number[],
  data?: { category_path?: string },
): Promise<{
  ok: boolean
  success_count: number
  failed_count: number
  results: { id: number; success: boolean; path?: string; error?: string }[]
}> {
  return http.post('/review/batch-approve', { doc_ids, ...(data || {}) })
}

// ---------- 分类管理 ----------
export interface CategoryNode {
  id: number
  parent_id: number | null
  name: string
  path: string
  description: string
  enabled: boolean
  sort_order: number
  children: CategoryNode[]
}

export function listCategories(): Promise<CategoryNode[]> {
  return http.get('/categories')
}

export function listCategoriesFlat(): Promise<CategoryNode[]> {
  return http.get('/categories/all')
}

export function createCategory(data: {
  name: string
  parent_id?: number | null
  description?: string
  sort_order?: number
}): Promise<CategoryNode> {
  return http.post('/categories', data)
}

export function updateCategory(id: number, data: Partial<CategoryNode>): Promise<CategoryNode> {
  return http.put(`/categories/${id}`, data)
}

export function deleteCategory(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/categories/${id}`)
}

// ---------- 规则 ----------
export interface Rule {
  id: number
  category_id: number
  keyword: string
  match_type: string
  priority: number
  weight: number
  enabled: boolean
}

export interface RenameTemplate {
  id: number
  category_id: number
  template: string
  enabled: boolean
}

export function listRules(categoryId?: number): Promise<Rule[]> {
  return http.get('/rules', { params: { category_id: categoryId } })
}

export function createRule(data: Partial<Rule>): Promise<Rule> {
  return http.post('/rules', data)
}

export function updateRule(id: number, data: Partial<Rule>): Promise<Rule> {
  return http.put(`/rules/${id}`, data)
}

export function deleteRule(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/rules/${id}`)
}

export function listTemplates(categoryId?: number): Promise<RenameTemplate[]> {
  return http.get('/rules/templates', { params: { category_id: categoryId } })
}

export function createTemplate(data: { category_id: number; template: string }): Promise<RenameTemplate> {
  return http.post('/rules/templates', data)
}

export function deleteTemplate(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/rules/templates/${id}`)
}

// ---------- 操作日志 ----------
export interface LogItem {
  id: number
  document_id: number | null
  job_id: number | null
  operation_type: string
  old_filename: string
  new_filename: string
  old_path: string
  new_path: string
  result: string
  error_message: string
  created_at?: string
}

export function listLogs(params: { operation_type?: string; skip?: number; limit?: number }): Promise<LogItem[]> {
  return http.get('/logs', { params })
}

export function undoLog(id: number): Promise<{ ok: boolean; restored_path?: string }> {
  return http.post(`/logs/${id}/undo`)
}

// ---------- 系统设置 ----------
export interface Settings {
  document_root: string
  inbox_root: string
  ocr_enabled: boolean
  ai_enabled: boolean
  ai_provider: string
  ai_base_url: string
  ai_model: string
  ai_api_key_set: boolean
  ai_api_key?: string
  ai_timeout: number
  ai_max_retries: number
  ai_max_text_length: number
  auto_archive_threshold: number
  review_threshold: number
  conf_rule_weight: number
  conf_field_weight: number
  conf_keyword_weight: number
  conf_ai_weight: number
  allow_overwrite: boolean
  host_bind: string
  access_password_set: boolean
  access_password?: string
  email_enabled: boolean
  email_imap_host: string
  email_imap_port: number
  email_user: string
  email_password_set: boolean
  email_password?: string
  email_poll_interval: number
}

export function getSettings(): Promise<Settings> {
  return http.get('/settings')
}

export function updateSettings(data: Partial<Settings>): Promise<Settings> {
  return http.put('/settings', data)
}

// ---------- 访问认证（局域网密码保护） ----------
export interface AuthStatus {
  password_required: boolean
}

export function getAuthStatus(): Promise<AuthStatus> {
  return http.get('/auth/status')
}

export function login(password: string): Promise<{ ok: boolean; token: string }> {
  return http.post('/auth/login', { password })
}

// ---------- 邮箱接收 ----------
export interface EmailStatus {
  running: boolean
  enabled: boolean
  last_check?: string
  last_error?: string | null
  last_count?: number
}

export function getEmailStatus(): Promise<EmailStatus> {
  return http.get('/email/status')
}

export function checkEmailNow(): Promise<{ ok: boolean; count: number; status: EmailStatus }> {
  return http.post('/email/check')
}

// ---------- 文件名规则（按文件名直接归档） ----------
export interface FilenameRule {
  id: number
  category_id: number
  pattern: string
  priority: number
  enabled: boolean
  note?: string
  category_name?: string
  category_path?: string
}

export function listFilenameRules(categoryId?: number): Promise<FilenameRule[]> {
  return http.get('/filename-rules', { params: { category_id: categoryId } })
}

export function createFilenameRule(data: { category_id: number; pattern: string; priority?: number; note?: string }): Promise<FilenameRule> {
  return http.post('/filename-rules', data)
}

export function updateFilenameRule(id: number, data: Partial<FilenameRule>): Promise<FilenameRule> {
  return http.put(`/filename-rules/${id}`, data)
}

export function deleteFilenameRule(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/filename-rules/${id}`)
}

