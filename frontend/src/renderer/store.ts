import { create } from 'zustand'
import { TaskStatus, VideoConfig } from './api/client'
import { ToastItem } from './components/animation/Toast'
import { collectTaskLogs } from './taskLogs'
import { defaultBgmTrack, migrateBgmConfig } from './bgmConfig'

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
  logCursors: Record<string, number>
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
  bgm_tracks: { full: defaultBgmTrack(), hook: defaultBgmTrack(false), body: defaultBgmTrack(false) },
  finished_hook: false,
  duration_mode: 'clips',
  voice_dir: '',
  srt_dir: '',
  watermark_path: '',
  base_out_dir: '',
  t_hook: 3.0,
  hook_full_duration: false,
  t_body: 3.0,
  body_full_duration: false,
  total_clips: 5,
  target_count: 10,
  hook_r: 0.5,
  body_r: 0.5,
  bgm_r: 0.3,
  resolution: '1080*1920',
  fps: '30',
  bitrate: '8000k',
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
    const migration = migrateBgmConfig(saved)
    const config: VideoConfig = {
      ...defaultConfig,
      ...saved,
      bgm_tracks: migration.tracks,
      bgm_dir: migration.tracks.full.enabled ? migration.tracks.full.path : '',
      duration_mode: migration.durationMode,
      finished_hook: saved.finished_hook ?? (!saved.apply_bgm_to_hook && !saved.apply_voice_to_hook && !saved.apply_srt_to_hook && !saved.apply_watermark_to_hook),
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
    if (!saved.bgm_tracks) {
      localStorage.setItem('vm-config', JSON.stringify(config))
      if (migration.notice) localStorage.setItem('vm-bgm-migration-notice', migration.notice)
    }
    return config
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
      if (config.bgm_tracks) {
        // Keep legacy display/probing fields mirrored from the full track.
        // Never infer a music scope from the finished-Hook preset.
        const tracks = { ...config.bgm_tracks }
        if (partial.bgm_dir !== undefined && !partial.bgm_tracks) tracks.full = { ...tracks.full, path: partial.bgm_dir }
        if (partial.vol_bgm !== undefined && !partial.bgm_tracks) tracks.full = { ...tracks.full, volume: partial.vol_bgm }
        if (partial.bgm_r !== undefined && !partial.bgm_tracks) tracks.full = { ...tracks.full, overlap: partial.bgm_r }
        config.bgm_tracks = tracks
        config.bgm_dir = tracks.full.enabled ? tracks.full.path : ''
        config.vol_bgm = tracks.full.volume
        config.bgm_r = tracks.full.overlap
        config.apply_bgm_to_hook = true
      }
      if (!config.bgm_dir.trim()) config.duration_mode = 'clips'
      localStorage.setItem('vm-config', JSON.stringify(config))
      return { config }
    }),

  tasks: [],
  currentTaskId: null,
  setTasks: (tasks) => set(state => {
    const { cursors, lines } = collectTaskLogs(tasks, state.logCursors)
    return { tasks, logCursors: cursors, logs: [...state.logs, ...lines] }
  }),
  setCurrentTaskId: (id) => set({ currentTaskId: id }),

  logs: [],
  logCursors: {},
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
