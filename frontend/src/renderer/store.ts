import { create } from 'zustand'
import { TaskStatus, VideoConfig } from './api/client'
import { ToastItem } from './components/animation/Toast'

type Page = 'dashboard' | 'sources' | 'mix' | 'queue' | 'output'

interface AppState {
  currentPage: Page
  setPage: (page: Page) => void

  config: VideoConfig
  setConfig: (config: Partial<VideoConfig>) => void

  tasks: TaskStatus[]
  currentTaskId: string | null
  setTasks: (tasks: TaskStatus[]) => void
  setCurrentTaskId: (id: string | null) => void

  logs: string[]
  appendLog: (line: string) => void
  clearLogs: () => void

  logDrawerOpen: boolean
  toggleLogDrawer: () => void

  backendReady: boolean
  setBackendReady: (v: boolean) => void

  scannedFiles: Record<string, { count: number; totalDuration: number }>
  setScannedFiles: (files: Record<string, { count: number; totalDuration: number }>) => void

  toasts: ToastItem[]
  addToast: (message: string, type?: ToastItem['type']) => void
  removeToast: (id: string) => void
}

const defaultConfig: VideoConfig = {
  task_name: 'Task',
  hook_dir: '',
  body_dirs: [],
  body_mode: 'normal',
  body_groups: Array.from({ length: 4 }, (_, index) => ({ enabled: index === 0, folder: '', clip_count: 1, clip_duration: 3 })),
  bgm_dir: '',
  voice_dir: '',
  srt_dir: '',
  watermark_path: '',
  base_out_dir: '',
  t_hook: 3.0,
  t_body: 3.0,
  total_clips: 5,
  target_count: 10,
  hook_r: 0.5,
  body_r: 0.5,
  bgm_r: 0.3,
  resolution: '1080*1920',
  fps: '30',
  bitrate: '5000k',
  vol_orig: 80,
  vol_hook_orig: 80,
  vol_bgm: 30,
  vol_voice: 100,
  apply_bgm_to_hook: true,
  apply_voice_to_hook: true,
  apply_srt_to_hook: true,
  apply_watermark_to_hook: true,
  enable_srt: false,
  subtitle_y_percent: 92,
  subtitle_font_size_percent: 5.6,
  enable_gpu: true,
  concurrent_tasks: 3,
  enable_variants: false,
  variant_strength: 'balanced',
  variant_hook: true,
  variant_body: true,
  variant_mirror: false,
  variant_frame_mix: true,
  variant_seed: null,
  enable_random_cover: false,
  random_cover_mode: 'replace',
}

function loadSavedConfig(): VideoConfig {
  try {
    const raw = localStorage.getItem('vm-config')
    if (!raw) return { ...defaultConfig }
    const saved = JSON.parse(raw)
    return {
      ...defaultConfig,
      ...saved,
      body_mode: saved.body_mode === 'grouped' ? 'grouped' : 'normal',
      body_groups: Array.from({ length: 4 }, (_, index) => ({
        ...defaultConfig.body_groups[index],
        ...(Array.isArray(saved.body_groups) ? saved.body_groups[index] : {}),
      })),
      enable_random_cover: Boolean(saved.enable_random_cover),
      random_cover_mode: saved.random_cover_mode === 'insert' ? 'insert' : 'replace',
      vol_hook_orig: saved.vol_hook_orig ?? saved.vol_orig ?? defaultConfig.vol_hook_orig,
      // V1 exposes a single final-output transformer, so legacy hidden scope/random
      // options must not silently alter the new behavior.
      variant_hook: true,
      variant_body: true,
      variant_mirror: false,
      variant_frame_mix: true,
    }
  } catch {
    return { ...defaultConfig }
  }
}

let toastId = 0

export const useStore = create<AppState>((set) => ({
  currentPage: 'dashboard',
  setPage: (page) => set({ currentPage: page }),

  config: loadSavedConfig(),
  setConfig: (partial) =>
    set((state) => {
      const config = { ...state.config, ...partial }
      localStorage.setItem('vm-config', JSON.stringify(config))
      return { config }
    }),

  tasks: [],
  currentTaskId: null,
  setTasks: (tasks) => set({ tasks }),
  setCurrentTaskId: (id) => set({ currentTaskId: id }),

  logs: [],
  appendLog: (line) => set((state) => ({ logs: [...state.logs, line] })),
  clearLogs: () => set({ logs: [] }),

  logDrawerOpen: false,
  toggleLogDrawer: () => set((state) => ({ logDrawerOpen: !state.logDrawerOpen })),

  backendReady: false,
  setBackendReady: (v) => set({ backendReady: v }),

  scannedFiles: {},
  setScannedFiles: (files) => set({ scannedFiles: files }),

  toasts: [],
  addToast: (message, type = 'info') =>
    set((state) => ({
      toasts: [...state.toasts, { id: `toast-${++toastId}`, message, type }],
    })),
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
}))
