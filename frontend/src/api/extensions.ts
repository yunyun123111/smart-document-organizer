import http from './http'

// ---------- 功能扩展包（OCR / OpenCV 按需下载） ----------
export interface ExtensionState {
  phase: 'idle' | 'downloading' | 'installing' | 'done' | 'error' | string
  progress: number
  message: string
  error: string
  updated_at: string
}

export interface ExtensionItem {
  name: string
  title: string
  summary: string
  detail: string
  installed: boolean
  active: boolean
  version: string
  installed_at: string
  size: number
  size_hint: number
  remote_version: string
  remote_size: number
  update_available: boolean
  remote_ok: boolean
  pending_delete: boolean
  state: ExtensionState
  runtime: { ocr: boolean; seal: boolean }
  requires_restart: boolean
}

export interface ExtensionListResp {
  items: ExtensionItem[]
  extensions_dir: string
  data_home: string
}

export function listExtensions(): Promise<ExtensionListResp> {
  return http.get('/extensions')
}

export function installExtension(name: string): Promise<{ started: boolean; reason?: string }> {
  return http.post(`/extensions/${name}/install`)
}

export function getExtensionProgress(name: string): Promise<{ name: string; state: ExtensionState }> {
  return http.get(`/extensions/${name}/progress`)
}

export function uninstallExtension(
  name: string,
): Promise<{ removed: boolean; pending: boolean; reason?: string }> {
  return http.post(`/extensions/${name}/uninstall`)
}
