const getBaseUrl = async (): Promise<string> => {
  if (window.electronAPI) {
    const port = await window.electronAPI.getBackendPort()
    return `http://127.0.0.1:${port}/api`
  }
  return 'http://127.0.0.1:8765/api'
}

function formatApiError(detail: unknown, fallback: string): string {
  const fieldLabels: Record<string, string> = {
    hook_r: 'Hook 重叠率', body_r: 'Body 重叠率', bgm_r: 'BGM 重叠率',
    t_hook: '首段时长', t_body: '后段时长', total_clips: '片段数',
    target_count: '生成数量', concurrent_tasks: '并发数', vol_hook_orig: 'Hook 原声音量',
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const error = item as { loc?: unknown[]; msg?: string }
        const field = error.loc?.at(-1)
        const label = field ? fieldLabels[String(field)] || String(field) : ''
        return label && error.msg ? `${label}：${error.msg}` : error.msg
      })
      .filter((message): message is string => Boolean(message))
    if (messages.length) return messages.join('；')
  }
  return fallback
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(formatApiError(err.detail, `请求失败（HTTP ${res.status}）`))
  }
  return res.json()
}

export interface VideoConfig {
  task_name: string
  hook_dir: string
  body_dirs: string[]
  bgm_dir: string
  voice_dir?: string
  srt_dir?: string
  watermark_path?: string
  base_out_dir: string
  t_hook: string | number
  t_body: string | number
  total_clips: string | number
  target_count: string | number
  hook_r: string | number
  body_r: string | number
  bgm_r: string | number
  resolution: string
  fps: string | number
  bitrate: string
  vol_orig: string | number
  vol_hook_orig: string | number
  vol_bgm: string | number
  vol_voice: string | number
  apply_bgm_to_hook: boolean
  apply_voice_to_hook: boolean
  apply_srt_to_hook: boolean
  apply_watermark_to_hook: boolean
  enable_srt: boolean
  subtitle_y_percent: number
  subtitle_font_size_percent: number
  enable_gpu: boolean
  concurrent_tasks?: string | number
  enable_variants: boolean
  variant_strength: 'mild' | 'balanced' | 'strong'
  variant_hook: boolean
  variant_body: boolean
  variant_mirror: boolean
  variant_frame_mix: boolean
  variant_seed?: number | null
}

export interface TaskStatus {
  task_id: string
  task_name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'stopped'
  progress: number
  current: number
  total: number
  message: string
  log_lines: string[]
  created_at: string
  updated_at?: string
  output_files: string[]
  output_elapsed?: Record<string, number>
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  createTask: (config: VideoConfig) =>
    request<{ task_id: string; message: string }>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ config }),
    }),

  listTasks: () => request<TaskStatus[]>('/tasks'),

  getTask: (taskId: string) => request<TaskStatus>(`/tasks/${taskId}`),

  stopTask: (taskId: string) =>
    request<{ message: string }>(`/tasks/${taskId}/stop`, { method: 'POST' }),

  getLogs: (taskId: string) => request<{ logs: string[] }>(`/tasks/${taskId}/logs`),

  streamLogs: async (taskId: string, onLog: (data: any) => void) => {
    const baseUrl = await getBaseUrl()
    const eventSource = new EventSource(`${baseUrl}/tasks/${taskId}/stream`)
    eventSource.onmessage = (e) => {
      if (e.data === '[DONE]') {
        eventSource.close()
        return
      }
      try {
        onLog(JSON.parse(e.data))
      } catch {
        onLog({ log: e.data })
      }
    }
    eventSource.onerror = () => eventSource.close()
    return () => eventSource.close()
  },

  scanDirectory: (dirPath: string, extensions: string[] = ['.mp4', '.mov']) =>
    request<{ files: string[]; count: number }>('/scan', {
      method: 'POST',
      body: JSON.stringify({ dir_path: dirPath, extensions }),
    }),

  probeFile: (filePath: string) =>
    request<any>(`/probe?file_path=${encodeURIComponent(filePath)}`, { method: 'POST' }),

  benchmark: (config: VideoConfig) =>
    request<any>('/benchmark', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  preflight: (config: VideoConfig) =>
    request<any>('/preflight', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  clearHistory: () =>
    request<{ message: string }>('/history/clear', { method: 'POST' }),
}
