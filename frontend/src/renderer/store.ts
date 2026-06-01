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
  vol_bgm: 30,
  vol_voice: 100,
  enable_srt: false,
  enable_gpu: true,
  concurrent_tasks: 3,
}

let toastId = 0

export const useStore = create<AppState>((set) => ({
  currentPage: 'dashboard',
  setPage: (page) => set({ currentPage: page }),

  config: { ...defaultConfig },
  setConfig: (partial) =>
    set((state) => ({ config: { ...state.config, ...partial } })),

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
