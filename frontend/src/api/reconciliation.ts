import http from './http'

// ---------- 业务线台账自动对账（V6 + P0/P1/P2 增强） ----------

export type DiffResolutionType =
  | 'ignore'
  | 'false_positive'
  | 'supplement'
  | 'adjust'
  | 'pending'
  | 'reopen'

export interface ReconciliationResolution {
  type?: DiffResolutionType | string
  note?: string
  at?: string
}

export interface ReconciliationDiff {
  type: string
  direction: string
  role: string
  severity: 'CRITICAL' | 'WARNING' | 'INFO' | string
  title: string
  expected: string
  actual: string
  delta: string
  message: string
  document_ids: number[]
  candidate_document_ids: number[]
  ignored: boolean
  key: string
  is_new: boolean | null
  resolution: ReconciliationResolution
}

export interface ResolvedDiff {
  key: string
  type: string
  direction: string
  role: string
  title: string
  expected: string
  actual: string
  resolved_at: string
}

export interface ReconciliationMatrixCell {
  direction: 'up' | 'down' | string
  role: string
  count: number
  amount: number
  document_ids: number[]
  candidate_count: number
}

export interface ReconciliationReport {
  id: number
  business_no: string
  ledger_sheet: string
  up_contract_no: string
  down_contract_no: string
  up_settlement: number
  down_settlement: number
  interest_receivable: number
  interest_received: number
  status: 'ok' | 'warning' | 'error' | 'no_data' | string
  diff_count: number
  checked_at: string | null
}

export interface ReconciliationDetail extends ReconciliationReport {
  vessel: string
  material: string
  supplier: string
  customer: string
  note: string
  up_contract_amount: number
  down_contract_amount: number
  matrix: ReconciliationMatrixCell[]
  diffs: ReconciliationDiff[]
  resolved_diffs: ResolvedDiff[]
  unlinked_document_ids: number[]
}

export interface ReconciliationList {
  total: number
  items: ReconciliationReport[]
}

export interface ReconciliationRunResult {
  total: number
  ok: number
  warning: number
  error: number
  no_data: number
  unlinked: number
  ledger_sheets: string[]
  duration_ms: number
  message: string
}

export interface UnlinkedDocument {
  document_id: number
  filename: string
  document_type: string
  contract_no: string
  role: string
  direction: string
  reason: string
}

export interface UnlinkedList {
  total: number
  items: UnlinkedDocument[]
}

// ---------- 外部明细（参考口径） ----------

export interface ExternalDetail {
  id: number
  batch_no: string
  business_no: string
  occurred_on: string | null
  amount: number
  kind: 'receipt' | 'payment' | string
  note: string
  source_filename: string
  imported_at: string | null
}

export interface ExternalDetailList {
  total: number
  receipt_total: number
  payment_total: number
  items: ExternalDetail[]
}

export interface ExternalBatch {
  batch_no: string
  source_filename: string
  imported_at: string
  rows: number
  receipt: string
  payment: string
}

export interface ExternalImportResult {
  batch_no: string
  imported: number
  skipped: number
  source_filename: string
  columns: Record<string, string>
  message: string
}

// ---------- 催办 / 聚合 / 巡检 ----------

export interface UrgeItem {
  business_no: string
  customer: string
  period: string
  status: string
  missing_roles: string[]
  requested_since: string
  urge_count: number
  last_urged_at: string
  last_note: string
}

export interface SummaryRow {
  key: string
  label: string
  business_count: number
  up_settlement: number
  down_settlement: number
  interest_received: number
  pay_amount: number
  receipt_amount: number
  external_receipt: number
  diff_count: number
  error_count: number
  warning_count: number
  no_data_count: number
}

export interface SummaryResult {
  group: string
  total: number
  rows: SummaryRow[]
}

export interface ReconcileSchedule {
  enabled: boolean
  cron: string
  last_run_at: string
  last_result: string
  changed_businesses: string[]
  last_error: string
}

export interface InvoiceUniquenessItem {
  invoice_no: string
  business_nos: string[]
  title: string
}

// ---------- 请求 ----------

export function runReconciliation(data: {
  business_no?: string
  source_id?: number
}): Promise<ReconciliationRunResult> {
  return http.post('/reconciliation/run', data)
}

export function listReconciliation(params: {
  status?: string
  keyword?: string
  page?: number
  page_size?: number
}): Promise<ReconciliationList> {
  return http.get('/reconciliation/reports', { params })
}

export function getReconciliationDetail(businessNo: string): Promise<ReconciliationDetail> {
  return http.get(`/reconciliation/reports/${encodeURIComponent(businessNo)}`)
}

/** 差异处理：优先按 key 定位（run 生成的稳定标识）。 */
export function resolveReconciliationDiff(
  businessNo: string,
  data: { key?: string; diff_type?: string; index?: number; resolution: DiffResolutionType | string; note?: string },
): Promise<ReconciliationReport> {
  return http.post(`/reconciliation/reports/${encodeURIComponent(businessNo)}/resolve`, data)
}

export function urgeReconciliationBusiness(
  businessNo: string,
  note = '',
): Promise<{ business_no: string; count: number; last_at: string; message: string }> {
  return http.post(`/reconciliation/reports/${encodeURIComponent(businessNo)}/urge`, { note })
}

export function listUrgeItems(): Promise<{ total: number; items: UrgeItem[] }> {
  return http.get('/reconciliation/urge-list')
}

export function listUnlinkedDocuments(sourceId?: number): Promise<UnlinkedList> {
  return http.get('/reconciliation/unlinked', { params: { source_id: sourceId } })
}

/** 导出 Excel（四 sheet），返回 Blob，由调用方落盘。 */
export function exportReconciliation(params: {
  status?: string
  keyword?: string
}): Promise<Blob> {
  return http.get('/reconciliation/export', { params, responseType: 'blob' })
}

export function importExternalDetails(
  file: File,
  mapping: Record<string, string> = {},
): Promise<ExternalImportResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('mapping', JSON.stringify(mapping))
  return http.post('/reconciliation/external-details/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

export function listExternalDetails(params: {
  business_no?: string
  batch_no?: string
  limit?: number
}): Promise<ExternalDetailList> {
  return http.get('/reconciliation/external-details', { params })
}

export function listExternalBatches(): Promise<{ total: number; items: ExternalBatch[] }> {
  return http.get('/reconciliation/external-details/batches')
}

export function deleteExternalBatch(batchNo: string): Promise<{ batch_no: string; removed: number }> {
  return http.delete(`/reconciliation/external-details/batches/${encodeURIComponent(batchNo)}`)
}

export function summaryByGroup(group: 'customer' | 'month'): Promise<SummaryResult> {
  return http.get('/reconciliation/summary', { params: { group } })
}

export function getReconcileSchedule(): Promise<ReconcileSchedule> {
  return http.get('/reconciliation/schedule')
}

export function updateReconcileSchedule(data: {
  enabled?: boolean
  cron?: string
}): Promise<ReconcileSchedule> {
  return http.put('/reconciliation/schedule', data)
}

export function clearReconcileChanges(): Promise<{ changed_businesses: string[] }> {
  return http.post('/reconciliation/schedule/clear-changes')
}

export function listInvoiceUniqueness(): Promise<{ total: number; items: InvoiceUniquenessItem[] }> {
  return http.get('/reconciliation/invoice-uniqueness')
}
