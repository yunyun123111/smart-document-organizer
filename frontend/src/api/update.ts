import http from './http'

// ---------- 版本更新（桌面安装版） ----------
export interface LatestVersion {
  build: string
  version: string
  size: number
  notes: string
}

export interface VersionInfo {
  supported: boolean
  current_build: string
  current_version?: string
  latest: LatestVersion | null
  has_update: boolean
  message: string
}

export interface UpdateState {
  phase: 'idle' | 'downloading' | 'launching' | 'error' | string
  progress: number
  message: string
  error: string
  updated_at: string
}

export function getVersion(refresh = false): Promise<VersionInfo> {
  return http.get('/system/version', { params: refresh ? { refresh: true } : {} })
}

export function getUpdateProgress(): Promise<{ state: UpdateState }> {
  return http.get('/system/update/progress')
}

export function startUpdate(): Promise<{ started: boolean; reason?: string }> {
  return http.post('/system/update')
}
