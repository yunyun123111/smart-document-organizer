import http from './http'

// ---------- 业务线台账自动对账（V6） ----------

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
  status: 'ok' | 'warning' | 'error' | string
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

export function resolveReconciliationDiff(
  businessNo: string,
  data: { diff_type: string; index: number },
): Promise<ReconciliationReport> {
  return http.post(`/reconciliation/reports/${encodeURIComponent(businessNo)}/resolve`, data)
}

export function listUnlinkedDocuments(sourceId?: number): Promise<UnlinkedList> {
  return http.get('/reconciliation/unlinked', { params: { source_id: sourceId } })
}
