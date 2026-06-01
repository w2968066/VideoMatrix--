import { useState, useEffect, useRef } from 'react'
import { useStore } from '../store'
import { api, TaskStatus } from '../api/client'
import { Slider } from './ui/slider'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Checkbox } from './ui/checkbox'
import { BarPrimary, BarSecondary } from './ActionDock'
import { AssetCard } from './AssetCard'

// ─── icons ────────────────────────────────────────────────────────────────────
const PlayIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
    <path d="M8 5v14l11-7z" />
  </svg>
)
const RadarIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.5" fill="currentColor" />
    <path d="M12 12l5-5" />
  </svg>
)
const GaugeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 14l4-4" />
    <path d="M3.5 16a9 9 0 1117 0" />
    <circle cx="12" cy="14" r="1.4" fill="currentColor" stroke="none" />
  </svg>
)

// ─── Group ────────────────────────────────────────────────────────────────────
function Group({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <h2 className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mb-3">{title}</h2>
      {children}
    </div>
  )
}

// ─── Slider row (compact) ─────────────────────────────────────────────────────
function SliderRow({ label, value, min, max, step, suffix, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  suffix?: string
  onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center gap-3 h-8">
      <label className="w-14 shrink-0 text-[11px] text-muted-foreground">{label}</label>
      <div className="flex-1">
        <Slider value={[value]} min={min} max={max} step={step} onValueChange={([v]) => onChange(v)} />
      </div>
      <span className="font-mono text-[11px] text-foreground tabular-nums w-12 text-right">
        {Number.isInteger(value) ? value : value.toFixed(2)}
        {suffix && <span className="text-muted-foreground ml-0.5">{suffix}</span>}
      </span>
    </div>
  )
}

// ─── Select row (compact) ─────────────────────────────────────────────────────
function SelectRow({ label, value, onChange, options }: {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <label className="w-9 shrink-0 text-[11px] text-muted-foreground">{label}</label>
      <div className="flex-1 min-w-0">
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {options.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
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
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logs])

  const running = tasks.filter(t => t.status === 'running').length
  const allOutputFiles = tasks.flatMap(t => t.output_files)

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
    if (!config.hook_dir || !config.bgm_dir || !config.base_out_dir) {
      addToast('请填写 Hook、BGM 和输出目录', 'warning'); return
    }
    setIsRunning(true)
    try {
      const bodyDirs = config.body_dirs.length > 0 ? config.body_dirs : [config.hook_dir]
      const res = await api.createTask({ ...config, body_dirs: bodyDirs })
      addToast('任务已启动', 'success')
      appendLog(`▸ ${new Date().toLocaleTimeString()} 任务 ${res.task_id} 已派发`)
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    } finally { setIsRunning(false) }
  }

  const preFlight = async () => {
    try { await api.createTask({ ...config, target_count: 1 }); addToast('预检已提交', 'info') }
    catch (e: any) { addToast(e.message, 'error') }
  }

  const benchmark = async () => {
    try {
      const res = await api.benchmark(config)
      if (res.error) { addToast(res.error, 'error'); return }
      setConfig({ concurrent_tasks: res.best_concurrent })
      addToast(`最优并发 ${res.best_concurrent} 路`, 'success')
    } catch (e: any) { addToast(e.message, 'error') }
  }

  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col w-full h-full overflow-hidden">
      {/* macOS hidden-titlebar drag region */}
      <div className="h-7 w-full shrink-0" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties} />

      <div className="flex-1 min-h-0 px-10 pb-4 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-4 pb-3 mb-3 border-b border-white/[0.06] shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-sm font-semibold text-foreground tracking-wide">VideoMatrix</h1>
            <span className="shrink-0 rounded-[4px] border border-accent/45 bg-accent px-3 py-1 text-[12px] font-semibold leading-none text-background shadow-[0_0_0_1px_rgba(232,166,88,0.18),0_8px_24px_-14px_rgba(232,166,88,0.9)]">
              VX：18667026883
            </span>
          </div>
          <span className="shrink-0 text-[11px] font-mono text-muted-foreground">
            {running > 0 ? (
              <span className="text-accent inline-flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-accent animate-pulse-dot" />
                {running} 运行中
              </span>
            ) : '就绪'}
          </span>
        </div>

        {/* TWO COLUMNS: left controls / right telemetry */}
        <div className="flex-1 min-h-0 grid grid-cols-[1fr_420px] gap-7">

          {/* ─── LEFT: configuration ─── */}
          <div className="flex flex-col gap-5 min-h-0 overflow-hidden">

            {/* Sources — card grid */}
            <Group title="素材">
              <div className="grid grid-cols-4 gap-2.5">
                <AssetCard kind="hook"      label="Hook 首段" value={config.hook_dir}             count={scannedFiles.hook?.count} required
                  onBrowse={() => browse('hook_dir')}        onClear={() => setConfig({ hook_dir: '' })} />
                <AssetCard kind="body"      label="Body 后段" value={config.body_dirs.join('; ')} count={scannedFiles.body?.count}
                  onBrowse={() => browse('body_dirs', true)} onClear={() => setConfig({ body_dirs: [] })} />
                <AssetCard kind="bgm"       label="BGM 配乐"  value={config.bgm_dir}              count={scannedFiles.bgm?.count}  required
                  onBrowse={() => browse('bgm_dir')}         onClear={() => setConfig({ bgm_dir: '' })} />
                <AssetCard kind="voice"     label="配音"      value={config.voice_dir || ''}
                  onBrowse={() => browse('voice_dir')}       onClear={() => setConfig({ voice_dir: '' })} />
                <AssetCard kind="srt"       label="字幕"      value={config.srt_dir || ''}
                  onBrowse={() => browse('srt_dir')}         onClear={() => setConfig({ srt_dir: '' })} />
                <AssetCard kind="watermark" label="水印"      value={config.watermark_path || ''} pickAction="选择"
                  onBrowse={() => browseFile('watermark_path')} onClear={() => setConfig({ watermark_path: '' })} />
                <AssetCard kind="output"    label="输出目录"  value={config.base_out_dir} required
                  onBrowse={() => browse('base_out_dir')}    onClear={() => setConfig({ base_out_dir: '' })} />
              </div>
            </Group>

            {/* Parameters · overlap · volume — two-column slider grid inside a soft panel */}
            <div className="grid grid-cols-2 gap-x-7 gap-y-5 rounded-lg border border-white/[0.06] bg-gradient-to-b from-white/[0.018] to-white/[0.004] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
              <Group title="参数">
                <div className="space-y-1.5">
                  <SliderRow label="首段"  value={config.t_hook}    suffix="s" min={0.5} max={10}  step={0.5} onChange={(v) => setConfig({ t_hook: v })} />
                  <SliderRow label="后段"  value={config.t_body}    suffix="s" min={0.5} max={10}  step={0.5} onChange={(v) => setConfig({ t_body: v })} />
                  <SliderRow label="片段"  value={config.total_clips}            min={2}   max={20}  step={1}   onChange={(v) => setConfig({ total_clips: Math.floor(v) })} />
                  <SliderRow label="数量"  value={config.target_count}           min={1}   max={100} step={1}   onChange={(v) => setConfig({ target_count: Math.floor(v) })} />
                  <SliderRow label="并发"  value={config.concurrent_tasks || 3}  min={1}   max={8}   step={1}   onChange={(v) => setConfig({ concurrent_tasks: Math.floor(v) })} />
                </div>
              </Group>

              <Group title="重叠率 / 音量">
                <div className="space-y-1.5">
                  <SliderRow label="Hook"  value={config.hook_r}   min={0} max={0.99} step={0.05} onChange={(v) => setConfig({ hook_r: v })} />
                  <SliderRow label="Body"  value={config.body_r}   min={0} max={0.99} step={0.05} onChange={(v) => setConfig({ body_r: v })} />
                  <SliderRow label="BGM-R" value={config.bgm_r}    min={0} max={0.99} step={0.05} onChange={(v) => setConfig({ bgm_r: v })} />
                  <SliderRow label="原声"  value={config.vol_orig}  suffix="%" min={0} max={200} step={5} onChange={(v) => setConfig({ vol_orig: Math.floor(v) })} />
                  <SliderRow label="BGM"   value={config.vol_bgm}   suffix="%" min={0} max={200} step={5} onChange={(v) => setConfig({ vol_bgm: Math.floor(v) })} />
                </div>
              </Group>
            </div>

            {/* Output */}
            <Group title="输出">
              <div className="grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-3 items-center rounded-lg border border-white/[0.06] bg-gradient-to-b from-white/[0.018] to-white/[0.004] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
                <SelectRow label="分辨率" value={config.resolution} onChange={(v) => setConfig({ resolution: v })}
                  options={[
                    { value: '720*1280',  label: '720 × 1280' },
                    { value: '1080*1920', label: '1080 × 1920' },
                    { value: '1920*1080', label: '1920 × 1080' },
                    { value: '2160*3840', label: '2160 × 3840' },
                  ]} />
                <SelectRow label="码率" value={config.bitrate} onChange={(v) => setConfig({ bitrate: v })}
                  options={[
                    { value: '3000k',  label: '3 Mbps' },
                    { value: '5000k',  label: '5 Mbps' },
                    { value: '8000k',  label: '8 Mbps' },
                    { value: '12000k', label: '12 Mbps' },
                  ]} />
                <SelectRow label="帧率" value={String(config.fps)} onChange={(v) => setConfig({ fps: parseInt(v) })}
                  options={[
                    { value: '24', label: '24 fps' },
                    { value: '30', label: '30 fps' },
                    { value: '60', label: '60 fps' },
                  ]} />
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

            {/* Action row — pushed to bottom of the left column so it aligns
                with the right telemetry panel */}
            <div className="mt-auto pt-2 flex items-center gap-2.5">
              <BarPrimary label="启动渲染" loading={isRunning} onClick={startRender}>
                <PlayIcon />
              </BarPrimary>
              <BarSecondary label="预检产能" onClick={preFlight}>
                <RadarIcon />
              </BarSecondary>
              <BarSecondary label="智能压测" onClick={benchmark}>
                <GaugeIcon />
              </BarSecondary>
            </div>
          </div>

          {/* ─── RIGHT: telemetry panel ─── */}
          <div className="flex flex-col min-h-0 rounded-lg border border-white/[0.08] bg-gradient-to-b from-white/[0.02] to-white/[0.005] shadow-[0_8px_32px_-12px_rgba(0,0,0,0.6),inset_0_1px_0_rgba(255,255,255,0.04)] overflow-hidden">
            {/* Tab strip */}
            <div className="flex items-center border-b border-white/[0.06] px-3 shrink-0">
              {([
                { k: 'log',    label: '日志', count: logs.length },
                { k: 'tasks',  label: '任务', count: tasks.length },
                { k: 'output', label: '产出', count: allOutputFiles.length },
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
                  {allOutputFiles.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无产出
                    </div>
                  ) : (
                    allOutputFiles.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 py-1.5 border-b border-white/[0.04] last:border-0">
                        <span className="text-[10px] text-muted-foreground font-mono tabular-nums w-6">
                          {String(i + 1).padStart(2, '0')}
                        </span>
                        <span className="flex-1 text-[11px] text-foreground/85 font-mono truncate">
                          {f.split('/').pop()}
                        </span>
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

    </div>
  )
}
