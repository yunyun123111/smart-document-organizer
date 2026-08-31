import http from './http'

export interface SystemInfo {
  app: string
  document_root: string
  inbox_root: string
  ocr_enabled: boolean
  ai_enabled: boolean
  ai_provider: string
  auto_archive_threshold: number
  review_threshold: number
}

export interface HealthStatus {
  status: string
  app: string
  version: string
  environment: string
}

export function getHealth(): Promise<HealthStatus> {
  return http.get('/system/health')
}

export function getSystemInfo(): Promise<SystemInfo> {
  return http.get('/system/info')
}
