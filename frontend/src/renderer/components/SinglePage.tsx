import { useState, useEffect, useRef } from 'react'
import { useStore } from '../store'
import { api, TaskStatus } from '../api/client'
import { Checkbox } from './ui/checkbox'
import { AssetCard } from './AssetCard'

const RESOLUTION_PRESETS = [
  { label: '1080P', value: '1080*1920' },
  { label: '2K', value: '1440*2560' },
]

// ─── Group ────────────────────────────────────────────────────────────────────
function Group({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <h2 className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground mb-1">{title}</h2>
      {children}
    </div>
  )
}

// ─── Text parameter row (compact) ─────────────────────────────────────────────
function ParamRow({ label, value, suffix, onChange, placeholder }: {
  label: string
  value: string | number
  suffix?: string
  placeholder?: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex items-center gap-2 h-7">
      <label className="w-14 shrink-0 text-[11px] text-muted-foreground">{label}</label>
      <input
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode="decimal"
        className="h-7 min-w-0 flex-1 rounded-[4px] border border-white/[0.10] bg-[#111318] px-2.5 font-mono text-[11px] text-white outline-none placeholder:text-muted-foreground/55 focus:border-accent/70"
      />
      {suffix && <span className="w-7 shrink-0 text-[10px] text-muted-foreground">{suffix}</span>}
    </div>
  )
}

function getResolutionTag(value: string) {
  const normalized = value.toLowerCase().replace('*', 'x')
  const parts = normalized.split('x').map((v) => parseInt(v, 10))
  if (parts.length !== 2 || parts.some(Number.isNaN)) return '?'
  const pixels = parts[0] * parts[1]
  if (pixels >= 3840 * 2160 * 0.9) return '4K'
  if (pixels >= 2560 * 1440 * 0.9) return '2K'
  if (pixels >= 1920 * 1080 * 0.9) return '1080P'
  if (pixels >= 1280 * 720 * 0.9) return '720P'
  return '标清'
}

// ─── Task row ─────────────────────────────────────────────────────────────────
function TaskRow({ task }: { task: TaskStatus }) {
  const tone: Record<string, { bar: string; text: string; label: string }> = {
    pending:   { bar: 'bg-muted-foreground/40', text: 'text-muted-foreground', label: '等待' },
    running:   { bar: 'bg-accent',              text: 'text-accent',           label: '运行' },
    completed: { bar: 'bg-ok',                  text: 'text-ok',               label: '完成' },
    failed:    { bar: 'bg-hot',                 text: 'text-hot',              label: '失败' },
    stopped:   { bar: 'bg-muted-foreground/40', text: 'text-muted-foreground', label: '停止' },
  }
  const t = tone[task.status] || tone.pending
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-white/[0.04] last:border-0">
      <span className={`w-8 shrink-0 text-[10px] font-mono ${t.text}`}>{t.label}</span>
      <span className="flex-1 text-[11px] text-foreground/85 truncate">{task.task_name}</span>
      {task.total > 0 && (
        <>
          <div className="w-20 h-px bg-white/[0.08] overflow-hidden relative">
            <div className={`absolute inset-y-0 left-0 ${t.bar} transition-all duration-500`} style={{ width: `${task.progress}%` }} />
          </div>
          <span className="text-[10px] font-mono text-muted-foreground tabular-nums w-14 text-right">
            {task.current}/{task.total} · {task.progress}%
          </span>
        </>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function SinglePage() {
  const { config, setConfig, tasks, logs, appendLog, clearLogs, addToast, scannedFiles } = useStore()
  const [isRunning, setIsRunning] = useState(false)
  const [rightTab, setRightTab] = useState<'log' | 'tasks' | 'output'>('log')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('vm-theme') as 'dark' | 'light') || 'dark')
  const [benchmarkRunning, setBenchmarkRunning] = useState(false)
  const [benchmarkProgress, setBenchmarkProgress] = useState(0)
  const [completionNotice, setCompletionNotice] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const taskStatusRef = useRef<Record<string, string>>({})

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logs])

  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', theme === 'light')
    localStorage.setItem('vm-theme', theme)
  }, [theme])

  useEffect(() => {
    tasks.forEach((task) => {
      const previous = taskStatusRef.current[task.task_id]
      if (previous && previous !== task.status && task.status === 'completed') {
        addToast(`任务完成：${task.task_name}`, 'success')
        appendLog(`✅ ${new Date().toLocaleTimeString()} 任务完成：${task.task_name}`)
        setCompletionNotice(task.task_name)
        window.setTimeout(() => setCompletionNotice(null), 6500)
        setRightTab('output')
      }
      taskStatusRef.current[task.task_id] = task.status
    })
  }, [tasks, addToast, appendLog])

  const running = tasks.filter(t => t.status === 'running').length
  const allOutputItems = tasks.flatMap(t => t.output_files.map((file) => ({
    file,
    elapsed: t.output_elapsed?.[file],
  })))
  const setParam = (key: string, raw: string) => setConfig({ [key]: raw } as any)
  const splitPathList = (raw: string) =>
    raw.split(';').map(p => p.trim()).filter(Boolean)
  const openConfiguredPath = (raw: string, fileMode = false) => {
    const paths = splitPathList(raw)
    paths.forEach((p) => {
      if (!p) return
      const target = fileMode ? p.replace(/[\\/][^\\/]*$/, '') : p
      window.electronAPI?.openPath(target)
    })
  }
  const ensureRunConfig = () => {
    if (!config.hook_dir || !config.bgm_dir) {
      addToast('请填写 Hook 和 BGM', 'warning')
      return null
    }
    const bodyDirs = config.body_dirs.length > 0 ? config.body_dirs : [config.hook_dir]
    return { ...config, body_dirs: bodyDirs }
  }

  const browse = async (key: string, multi = false) => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const path = await window.electronAPI.openDirectory()
    if (!path) return
    if (key === 'body_dirs') setConfig({ body_dirs: multi ? [...config.body_dirs, path] : [path] })
    else setConfig({ [key]: path } as any)
  }

  const browseFile = async (key: string) => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const path = await window.electronAPI.openFile([{ name: 'Images', extensions: ['png', 'gif', 'jpg'] }])
    if (path) setConfig({ [key]: path } as any)
  }

  const startRender = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig) return
    setIsRunning(true)
    try {
      const res = await api.createTask(runConfig)
      addToast('任务已启动', 'success')
      appendLog(`▸ ${new Date().toLocaleTimeString()} 任务 ${res.task_id} 已派发`)
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    } finally { setIsRunning(false) }
  }

  const preFlight = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig) return
    try {
      const res = await api.preflight(runConfig)
      clearLogs()
      ;(res.report || []).forEach((item: any) => appendLog(`${item.ok ? '✅' : '❌'} [${item.name}] ${item.message}`))
      appendLog(`>>> 预检完成。总可用首段产能预估: ${res.capacity}`)
      addToast('预检完成', res.ok ? 'success' : 'error')
    }
    catch (e: any) { addToast(e.message, 'error') }
  }

  const benchmark = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig || benchmarkRunning) return

    setBenchmarkRunning(true)
    setBenchmarkProgress(1)
    appendLog('>>> [压测] 正在测试 1%')
    addToast('智能压测已开始', 'info')

    const timer = window.setInterval(() => {
      setBenchmarkProgress((p) => {
        const next = Math.min(95, p + 7)
        if (next % 14 === 0 || next === 95) appendLog(`>>> [压测] 正在测试 ${next}%`)
        return next
      })
    }, 900)

    try {
      const res = await api.benchmark(runConfig)
      if (res.error) { addToast(res.error, 'error'); return }
      setBenchmarkProgress(100)
      appendLog('>>> [压测] 智能压测完成')
      Object.values(res.results || {}).forEach((item: any) => {
        appendLog(`    - ${item.concurrent} 路并发总耗时: ${item.total_time} 秒，单视频平均: ${item.avg_per_video} 秒`)
      })
      appendLog(`✅ [压测完成] 最优节点为 ${res.best_concurrent} 路并发`)
      setConfig({ concurrent_tasks: res.best_concurrent })
      addToast(`最优并发 ${res.best_concurrent} 路`, 'success')
    } catch (e: any) {
      addToast(e.message, 'error')
    } finally {
      window.clearInterval(timer)
      window.setTimeout(() => {
        setBenchmarkRunning(false)
        setBenchmarkProgress(0)
      }, 800)
    }
  }

  const clearHistory = async () => {
    try {
      await api.clearHistory()
      addToast('历史记录已清理', 'success')
      appendLog(`▸ ${new Date().toLocaleTimeString()} 历史记录已清理`)
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    }
  }

  const stopRunning = async () => {
    const activeTasks = tasks.filter((t) => t.status === 'running' || t.status === 'pending')
    if (activeTasks.length === 0) {
      addToast('当前没有运行中的任务', 'info')
      return
    }
    try {
      await Promise.all(activeTasks.map((t) => api.stopTask(t.task_id)))
      appendLog(`▸ ${new Date().toLocaleTimeString()} 已发送停止指令`)
      addToast('停止指令已发送', 'success')
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    }
  }

  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="ui-shell flex flex-col overflow-hidden">
      {/* macOS hidden-titlebar drag region */}
      <div className="h-3 w-full shrink-0" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties} />

      <div className="flex-1 min-h-0 px-5 pb-2 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-4 pb-1.5 mb-1.5 border-b border-white/[0.055] shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-sm font-semibold text-foreground tracking-wide">VideoMatrix</h1>
            <span className="shrink-0 rounded-[4px] border border-accent/45 bg-accent px-3 py-1 text-[12px] font-semibold leading-none text-background shadow-[0_0_0_1px_rgba(232,166,88,0.18),0_8px_24px_-14px_rgba(232,166,88,0.9)]">
              VX：18667026883
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="h-7 rounded-[4px] border border-white/[0.10] px-2.5 text-[11px] text-foreground/85 hover:border-accent/60 hover:text-accent"
            >
              {theme === 'dark' ? 'Light' : 'Dark'}
            </button>
            <span className="min-w-16 text-right text-[11px] font-mono text-muted-foreground">
              {running > 0 ? (
                <span className="text-accent inline-flex items-center gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-accent animate-pulse-dot" />
                  {running} 运行中
                </span>
              ) : '就绪'}
            </span>
          </div>
        </div>

        {/* TWO COLUMNS: left controls / right telemetry */}
        <div className="flex-1 min-h-0 grid grid-cols-[minmax(660px,0.68fr)_minmax(360px,0.32fr)] gap-3">

          {/* ─── LEFT: configuration ─── */}
          <div className="flex flex-col gap-2 min-h-0 overflow-hidden">

            {/* Sources — long path inputs */}
            <Group title="素材">
              <div className="grid grid-cols-1 gap-1.5">
                <AssetCard kind="hook"      label="Hook 首段" value={config.hook_dir}             count={scannedFiles.hook?.count} required
                  onChange={(v) => setConfig({ hook_dir: v })}
                  onOpen={() => openConfiguredPath(config.hook_dir)}
                  onBrowse={() => browse('hook_dir')}        onClear={() => setConfig({ hook_dir: '' })} />
                <AssetCard kind="body"      label="Body 后段" value={config.body_dirs.join('; ')} count={scannedFiles.body?.count}
                  onChange={(v) => setConfig({ body_dirs: splitPathList(v) })}
                  onOpen={() => openConfiguredPath(config.body_dirs.join('; '))}
                  onBrowse={() => browse('body_dirs', true)} onClear={() => setConfig({ body_dirs: [] })} />
                <AssetCard kind="bgm"       label="BGM 配乐"  value={config.bgm_dir}              count={scannedFiles.bgm?.count}  required
                  onChange={(v) => setConfig({ bgm_dir: v })}
                  onOpen={() => openConfiguredPath(config.bgm_dir)}
                  onBrowse={() => browse('bgm_dir')}         onClear={() => setConfig({ bgm_dir: '' })} />
                <AssetCard kind="voice"     label="配音"      value={config.voice_dir || ''}
                  onChange={(v) => setConfig({ voice_dir: v })}
                  onOpen={() => openConfiguredPath(config.voice_dir || '')}
                  onBrowse={() => browse('voice_dir')}       onClear={() => setConfig({ voice_dir: '' })} />
                <AssetCard kind="srt"       label="字幕"      value={config.srt_dir || ''}
                  onChange={(v) => setConfig({ srt_dir: v })}
                  onOpen={() => openConfiguredPath(config.srt_dir || '')}
                  onBrowse={() => browse('srt_dir')}         onClear={() => setConfig({ srt_dir: '' })} />
                <AssetCard kind="watermark" label="水印"      value={config.watermark_path || ''} pickAction="选择"
                  onChange={(v) => setConfig({ watermark_path: v })}
                  onOpen={() => openConfiguredPath(config.watermark_path || '', true)}
                  onBrowse={() => browseFile('watermark_path')} onClear={() => setConfig({ watermark_path: '' })} />
                <AssetCard kind="output"    label="输出目录"  value={config.base_out_dir}
                  onChange={(v) => setConfig({ base_out_dir: v })}
                  onOpen={() => openConfiguredPath(config.base_out_dir)}
                  onBrowse={() => browse('base_out_dir')}    onClear={() => setConfig({ base_out_dir: '' })} />
              </div>
            </Group>

            {/* Parameters · overlap · volume · original action rail */}
            <div className="grid grid-cols-[minmax(0,1fr)_96px] gap-2.5 rounded-[6px] border border-white/[0.055] bg-white/[0.018] p-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 min-w-0">
                <Group title="参数">
                  <div className="space-y-1">
                    <ParamRow label="首段" value={config.t_hook} suffix="s" onChange={(v) => setParam('t_hook', v)} />
                    <ParamRow label="后段" value={config.t_body} suffix="s" onChange={(v) => setParam('t_body', v)} />
                    <ParamRow label="片段" value={config.total_clips} onChange={(v) => setParam('total_clips', v)} />
                    <ParamRow label="数量" value={config.target_count} onChange={(v) => setParam('target_count', v)} />
                    <ParamRow label="并发" value={config.concurrent_tasks ?? 3} onChange={(v) => setParam('concurrent_tasks', v)} />
                  </div>
                </Group>

                <Group title="重叠率 / 音量">
                  <div className="space-y-1">
                    <ParamRow label="Hook" value={config.hook_r} onChange={(v) => setParam('hook_r', v)} />
                    <ParamRow label="Body" value={config.body_r} onChange={(v) => setParam('body_r', v)} />
                    <ParamRow label="BGM-R" value={config.bgm_r} onChange={(v) => setParam('bgm_r', v)} />
                    <ParamRow label="原声" value={config.vol_orig} suffix="%" onChange={(v) => setParam('vol_orig', v)} />
                    <ParamRow label="BGM" value={config.vol_bgm} suffix="%" onChange={(v) => setParam('vol_bgm', v)} />
                  </div>
                </Group>
              </div>

              <div className="flex flex-col items-stretch justify-center gap-1 border-l border-white/[0.055] pl-2.5">
                <button type="button" onClick={preFlight} className="h-8 rounded-[4px] bg-accent px-2 text-[12px] font-semibold text-background hover:bg-accent-hover">
                  预检产能
                </button>
                <button type="button" onClick={startRender} disabled={isRunning} className="h-8 rounded-[4px] bg-accent px-2 text-[12px] font-semibold text-background hover:bg-accent-hover disabled:opacity-50">
                  {isRunning ? '启动中…' : '启动渲染'}
                </button>
                <button type="button" onClick={clearHistory} className="h-8 rounded-[4px] bg-accent px-2 text-[12px] font-semibold text-background hover:bg-accent-hover">
                  清除记录
                </button>
                <button type="button" onClick={stopRunning} disabled={running === 0} className="h-8 rounded-[4px] bg-hot px-2 text-[12px] font-semibold text-white hover:bg-hot/90 disabled:bg-hot/70 disabled:text-white/55">
                  停止
                </button>
                <button type="button" onClick={benchmark} disabled={benchmarkRunning} className="mt-0.5 h-6 rounded-[4px] border border-white/[0.10] bg-white/[0.01] px-2 text-[10px] text-muted-foreground hover:border-accent/60 hover:text-accent disabled:cursor-wait disabled:border-accent/50 disabled:text-accent">
                  {benchmarkRunning ? `压测中 ${benchmarkProgress}%` : '智能压测'}
                </button>
              </div>
            </div>

            {/* Output */}
            <Group title="输出" className="flex min-h-0 flex-1 flex-col">
              <div className="grid flex-1 grid-cols-2 gap-x-3 gap-y-1.5 items-center rounded-[6px] border border-white/[0.055] bg-white/[0.018] p-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="flex-1 min-w-0">
                    <ParamRow label="分辨率" value={config.resolution} placeholder="1080*1920" onChange={(v) => setConfig({ resolution: v })} />
                  </div>
                  <span className="w-12 shrink-0 font-mono text-[11px] text-accent">{getResolutionTag(config.resolution)}</span>
                  <div className="flex shrink-0 items-center gap-1">
                    {RESOLUTION_PRESETS.map((preset) => (
                      <button
                        key={preset.value}
                        type="button"
                        onClick={() => setConfig({ resolution: preset.value })}
                        className={`h-7 rounded-[4px] border px-2 text-[10px] font-semibold transition-colors ${
                          config.resolution === preset.value
                            ? 'border-accent bg-accent text-background'
                            : 'border-white/[0.10] bg-white/[0.02] text-muted-foreground hover:border-accent/60 hover:text-accent'
                        }`}
                        title={`9:16 ${preset.value}`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
                <ParamRow label="码率" value={config.bitrate} placeholder="5000k" onChange={(v) => setConfig({ bitrate: v })} />
                <ParamRow label="帧率" value={String(config.fps)} placeholder="29.97 / 30000/1001" onChange={(v) => setConfig({ fps: v as any })} />
                <div className="flex items-center gap-4 pl-2">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <Checkbox checked={config.enable_srt} onCheckedChange={(v) => setConfig({ enable_srt: v as boolean })} />
                    <span className="text-[11px] text-foreground/85">字幕</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <Checkbox checked={config.enable_gpu} onCheckedChange={(v) => setConfig({ enable_gpu: v as boolean })} />
                    <span className="text-[11px] text-foreground/85">GPU</span>
                  </label>
                </div>
              </div>
            </Group>

          </div>

          {/* ─── RIGHT: telemetry panel ─── */}
          <div className="flex flex-col min-h-0 rounded-[6px] border border-white/[0.07] bg-white/[0.018] shadow-[0_8px_32px_-18px_rgba(0,0,0,0.65),inset_0_1px_0_rgba(255,255,255,0.035)] overflow-hidden">
            {/* Tab strip */}
            <div className="flex items-center border-b border-white/[0.06] px-3 shrink-0">
              {([
                { k: 'log',    label: '日志', count: logs.length },
                { k: 'tasks',  label: '任务', count: tasks.length },
                { k: 'output', label: '产出', count: allOutputItems.length },
              ] as const).map(t => (
                <button
                  key={t.k}
                  onClick={() => setRightTab(t.k as any)}
                  className={`relative px-3 py-1.5 text-[11px] uppercase tracking-[0.18em] transition-colors ${
                    rightTab === t.k ? 'text-accent' : 'text-muted-foreground hover:text-foreground/80'
                  }`}
                >
                  {t.label}
                  <span className="ml-1.5 font-mono opacity-70 tabular-nums">{t.count}</span>
                  {rightTab === t.k && <span className="absolute left-3 right-3 -bottom-px h-px bg-accent" />}
                </button>
              ))}
              {rightTab === 'log' && logs.length > 0 && (
                <button
                  onClick={clearLogs}
                  className="ml-auto pr-2 text-[10px] text-muted-foreground hover:text-foreground transition-colors font-mono"
                >
                  清空
                </button>
              )}
            </div>

            {/* Tab panel */}
            <div className="flex-1 min-h-0 relative">
              {/* LOG */}
              {rightTab === 'log' && (
                <div ref={logRef} className="absolute inset-0 overflow-auto px-4 py-3 font-mono text-[11.5px] leading-[1.65] bg-black/25">
                  {logs.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无日志
                    </div>
                  ) : (
                    logs.map((l, i) => {
                      const isError = l.includes('错误') || l.toLowerCase().includes('error')
                      const isStart = l.startsWith('▸') || l.startsWith('>>>')
                      const isDone = l.startsWith('✅') || l.includes('完成')
                      const color =
                        isError ? 'text-hot' :
                        isStart ? 'text-accent' :
                        isDone ? 'text-ok' :
                        'text-foreground/75'
                      return (
                        <div key={i} className="flex gap-2.5">
                          <span className="text-muted/60 tabular-nums shrink-0 w-7 text-right select-none">
                            {String(i + 1).padStart(3, '0')}
                          </span>
                          <span className={`${color} whitespace-pre-wrap break-all`}>{l}</span>
                        </div>
                      )
                    })
                  )}
                </div>
              )}

              {/* TASKS */}
              {rightTab === 'tasks' && (
                <div className="absolute inset-0 overflow-auto px-4 py-2 bg-black/25">
                  {tasks.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无任务
                    </div>
                  ) : (
                    tasks.slice().reverse().map(t => <TaskRow key={t.task_id} task={t} />)
                  )}
                </div>
              )}

              {/* OUTPUT */}
              {rightTab === 'output' && (
                <div className="absolute inset-0 overflow-auto px-4 py-2 bg-black/25">
                  {allOutputItems.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无产出
                    </div>
                  ) : (
                    allOutputItems.map(({ file: f, elapsed }, i) => (
                      <div key={i} className="flex items-center gap-2 py-1.5 border-b border-white/[0.04] last:border-0">
                        <span className="text-[10px] text-muted-foreground font-mono tabular-nums w-6">
                          {String(i + 1).padStart(2, '0')}
                        </span>
                        <span className="flex-1 text-[11px] text-foreground/85 font-mono truncate">
                          {f.split('/').pop()}
                        </span>
                        {elapsed !== undefined && (
                          <span className="w-14 text-right text-[10px] font-mono text-accent">
                            {elapsed}s
                          </span>
                        )}
                        <button
                          onClick={() => window.electronAPI?.openPath(f)}
                          className="text-[10px] text-muted-foreground hover:text-accent transition-colors px-1.5"
                        >
                          打开
                        </button>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {completionNotice && (
        <div className="fixed left-1/2 top-16 z-[1200] -translate-x-1/2 rounded-[6px] border border-ok/50 bg-[#10180f] px-7 py-4 text-center shadow-[0_20px_60px_-20px_rgba(155,214,107,0.8)]">
          <div className="text-[16px] font-semibold text-ok">任务完成</div>
          <div className="mt-1 max-w-[520px] truncate text-[12px] text-foreground/80">{completionNotice}</div>
        </div>
      )}

    </div>
  )
}
