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

export interface TrendDaily {
  date: string
  new: number
  archived: number
  need_review: number
  processed: number
  failed: number
  duplicate: number
  success_rate: number | null
  fail_count: number
}

export interface TrendOcr {
  date: string
  total: number
  failed: number
  fail_rate: number | null
}

export interface DashboardAlert {
  level: string
  kind: string
  type: string | null
  message: string
  link: string
}

export interface DashboardTrends {
  days: number
  daily: TrendDaily[]
  ocr_daily: TrendOcr[]
  alerts: DashboardAlert[]
}

export function getDashboardTrends(): Promise<DashboardTrends> {
  return http.get('/system/dashboard/trends')
}

// ---------- 文件关联（重定位） ----------
export interface MissingDoc {
  id: number
  original_filename: string
  current_filename: string
  path: string
}

export interface RelocateStatus {
  total: number
  missing_count: number
  missing: MissingDoc[]
}

export interface RelocateResult {
  relinked: number
  failed: number
  total: number
  matched: { id: number; filename: string; new_path: string }[]
  unmatched: MissingDoc[]
  error?: string
}

export function getRelocateStatus(): Promise<RelocateStatus> {
  return http.get('/documents/relocate/status')
}

export function relocateDocuments(search_roots: string[], by_hash = false): Promise<RelocateResult> {
  return http.post('/documents/relocate', { search_roots, by_hash })
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

export function retryFailed(): Promise<ProcessingJob> {
  return http.post('/processing/retry-failed')
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
  duplicate_of?: string
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
  contract_no?: string
  amount_min?: number
  amount_max?: number
  date_start?: string
  date_end?: string
  skip?: number
  limit?: number
}): Promise<DocumentListItem[]> {
  return http.get('/documents', { params })
}

export interface SuggestItem {
  type: string
  text: string
}

export function suggestDocuments(keyword: string): Promise<{ suggestions: SuggestItem[] }> {
  return http.get('/documents/suggest', { params: { keyword } })
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

// 带鉴权下载文件流（axios 自动携带 token，避免新标签页 401）
export async function downloadDocumentFile(id: number): Promise<Blob> {
  const blob = (await http.get(`/documents/${id}/file`, { responseType: 'blob' })) as unknown as Blob
  return blob
}

// 带鉴权在新标签页打开原文件
export async function openDocumentFile(id: number): Promise<void> {
  const blob = await downloadDocumentFile(id)
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
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

export interface DocumentUpdatePayload {
  document_type?: string
  title?: string
  fields?: Record<string, string>
  filename?: string
}

// 已归档文档信息修正：文档类型 / 标题 / 识别字段 / 归档分类 / 文件名
export function updateDocument(id: number, payload: DocumentUpdatePayload): Promise<DocumentDetail> {
  return http.patch(`/documents/${id}`, payload)
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

// ---------- 格式样本（自动学习） ----------
export interface DocumentSampleItem {
  id: number
  document_type: string
  category_path: string
  original_filename: string
  usage_count: number
  enabled: boolean
  note: string | null
  created_at?: string | null
  summary?: {
    labels: number
    grams: number
    fields: string[]
    stats: Record<string, unknown>
  }
}

export function listSamples(): Promise<DocumentSampleItem[]> {
  return http.get('/samples')
}

export function createSample(formData: FormData): Promise<DocumentSampleItem> {
  // 大 PDF OCR 学习耗时较长，单独给 180s 超时，避免 30s 默认超时中断
  return http.post('/samples', formData, { timeout: 180000 })
}

export function updateSample(
  id: number,
  data: { document_type?: string; category_path?: string; enabled?: boolean; note?: string },
): Promise<DocumentSampleItem> {
  return http.patch(`/samples/${id}`, data)
}

export function deleteSample(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/samples/${id}`)
}

// ---------- 操作日志 ----------
export interface LogItem {  id: number
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


// ---------- 智能批处理：同类文件分组 ----------
export interface ReviewGroup {
  group_key: string
  group_label: string
  documents: DocumentListItem[]
}

export function listReviewGroups(): Promise<ReviewGroup[]> {
  return http.get('/review/groups')
}


// ---------- 备份 / 恢复 ----------
export interface BackupInfo {
  filename: string
  size: number
  created_at: string
  documents_count: number
  include_documents: boolean
}

export function listBackups(): Promise<{ ok: boolean; backups: BackupInfo[] }> {
  return http.get('/backup/list')
}

export function createBackup(includeDocuments = false): Promise<{ ok: boolean } & BackupInfo> {
  return http.post('/backup/create', { include_documents: includeDocuments })
}

export function deleteBackup(filename: string): Promise<{ ok: boolean }> {
  return http.delete(`/backup/${encodeURIComponent(filename)}`)
}

// 下载备份：走带 token 的 blob 下载（与文档打开一致，避免 401）
export async function downloadBackup(filename: string): Promise<void> {
  const token = localStorage.getItem('sdo_access_token') || ''
  const res = await fetch(`/api/backup/download/${encodeURIComponent(filename)}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(`下载失败: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// 恢复备份：上传 zip（高风险，会覆盖当前数据库）
export function restoreBackup(file: File): Promise<{ ok: boolean; restored_documents: number }> {
  const fd = new FormData()
  fd.append('file', file)
  return http.post('/backup/restore', fd)
}

// ---------- 配置状态校验 ----------
export interface ConfigCheckItem {
  key: string
  label: string
  level: 'ok' | 'warn' | 'error' | 'info'
  message: string
  detail: string
}
export interface ConfigCheckResult {
  summary: string
  ok_count: number
  warn_count: number
  error_count: number
  checks: ConfigCheckItem[]
}

export function getConfigCheck(): Promise<ConfigCheckResult> {
  return http.get('/system/config-check')
}

// ---------- 回收站（V1.5-01 删除安全化） ----------
export interface RecycleBinItem {
  id: number
  document_id: number
  original_filename: string
  current_filename: string
  original_path: string
  recycle_path: string
  file_hash: string
  file_size: number
  file_type: string
  document_type: string
  category_path: string
  original_status: string
  deleted_reason: string
  original_file_missing: boolean
  file_exists: boolean
  deleted_at?: string
}

export function listRecycleBin(): Promise<{ ok: boolean; items: RecycleBinItem[] }> {
  return http.get('/recycle-bin')
}

export function restoreRecycleItem(id: number): Promise<{ ok: boolean; message: string }> {
  return http.post(`/recycle-bin/${id}/restore`)
}

export function batchRestoreRecycle(ids: number[]): Promise<{
  ok: boolean
  restored_count: number
  failed_count: number
  restored: number[]
  errors: string[]
}> {
  return http.post('/recycle-bin/batch-restore', { ids })
}

export function permanentDeleteRecycleItem(id: number): Promise<{ ok: boolean; message: string }> {
  return http.delete(`/recycle-bin/${id}`)
}

export function batchPermanentDeleteRecycle(ids: number[]): Promise<{
  ok: boolean
  deleted_count: number
  failed_count: number
  errors: string[]
}> {
  return http.post('/recycle-bin/batch-delete', { ids })
}

export function emptyRecycleBin(): Promise<{ ok: boolean; ok_count: number; failed: number; errors: string[] }> {
  return http.post('/recycle-bin/empty')
}

// ---------- Excel 登记表（权威数据源） ----------
export interface ExcelSheetConfig {
  id: number
  source_id: number
  sheet_name: string
  enabled: boolean
  doc_type_hint: string
  column_map: Record<string, string>
  key_column: string
  row_count: number
  last_error: string
}

export interface ExcelSource {
  id: number
  name: string
  file_path: string
  enabled: boolean
  total_sheets: number
  sheets: ExcelSheetConfig[]
}

export function listExcelSources(): Promise<{ items: ExcelSource[] }> {
  return http.get('/excel-sources')
}

export function uploadExcelSource(file: File): Promise<{ source: ExcelSource }> {
  const fd = new FormData()
  fd.append('file', file)
  return http.post('/excel-sources/upload', fd)
}

export function updateExcelSource(id: number, body: { name?: string; enabled?: boolean }): Promise<{ source: ExcelSource }> {
  return http.put(`/excel-sources/${id}`, body)
}

export function updateExcelSheet(
  sourceId: number, sheetId: number,
  body: { enabled?: boolean; doc_type_hint?: string; key_column?: string; column_map?: Record<string, string> },
): Promise<{ sheet: ExcelSheetConfig }> {
  return http.put(`/excel-sources/${sourceId}/sheets/${sheetId}`, body)
}

export function deleteExcelSource(id: number): Promise<{ ok: boolean }> {
  return http.delete(`/excel-sources/${id}`)
}

export function testExcelMatch(id: number, fields: Record<string, string>): Promise<{ match: any }> {
  return http.post(`/excel-sources/${id}/test-match`, { fields })
}


// ---------- 业务档案（V2.0） ----------
export interface BusinessFileOut {
  id: number
  business_id: number
  document_id: number
  file_role: string
  is_primary: boolean
  link_source: string
  sort_order: number
  created_at?: string
  document_filename: string
  document_type: string
  file_type: string
}

export interface BusinessRecordOut {
  id: number
  business_no: string
  title: string
  business_type: string
  status: string
  ship_name: string
  counterparty: string
  total_amount: number
  sign_date?: string | null
  extra_data: string
  created_at?: string
  updated_at?: string
  file_count: number
  files: BusinessFileOut[]
}

export interface BusinessArchiveListResp {
  ok: boolean
  total: number
  items: BusinessRecordOut[]
}

export interface BusinessArchiveDetailResp {
  ok: boolean
  data: BusinessRecordOut
}

export interface BusinessArchiveCreate {
  business_no: string
  title?: string
  business_type?: string
  status?: string
  ship_name?: string
  counterparty?: string
  total_amount?: number
  sign_date?: string
  extra_data?: string
}

export interface BusinessArchiveUpdate {
  title?: string
  business_type?: string
  status?: string
  ship_name?: string
  counterparty?: string
  total_amount?: number
  sign_date?: string
  extra_data?: string
}

export interface BackfillResp {
  ok: boolean
  stats: Record<string, number>
}

export function listBusinessArchives(params: {
  status?: string
  business_type?: string
  keyword?: string
  skip?: number
  limit?: number
}): Promise<BusinessArchiveListResp> {
  return http.get('/business-archives', { params })
}

export function getBusinessArchive(id: number): Promise<BusinessArchiveDetailResp> {
  return http.get(`/business-archives/${id}`)
}

export function createBusinessArchive(payload: BusinessArchiveCreate): Promise<BusinessArchiveDetailResp> {
  return http.post('/business-archives', payload)
}

export function updateBusinessArchive(
  id: number, payload: BusinessArchiveUpdate,
): Promise<BusinessArchiveDetailResp> {
  return http.put(`/business-archives/${id}`, payload)
}

export function addBusinessFile(
  id: number, documentId: number, fileRole?: string,
): Promise<BusinessArchiveDetailResp> {
  return http.post(`/business-archives/${id}/files`, { document_id: documentId, file_role: fileRole })
}

export function removeBusinessFile(id: number, documentId: number): Promise<{ ok: boolean }> {
  return http.delete(`/business-archives/${id}/files/${documentId}`)
}

export function backfillBusinessArchives(): Promise<BackfillResp> {
  return http.post('/business-archives/backfill')
}
