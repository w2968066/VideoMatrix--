import { useState } from 'react'
import type { BgmScope, BgmTrack, BgmTracks } from '../api/client'
import { AssetCard } from './AssetCard'
import { Checkbox } from './ui/checkbox'

export const BGM_LABELS: Record<BgmScope, string> = { full: '全片 BGM', hook: 'Hook BGM', body: 'Body BGM' }

export function BgmPanel({ tracks, audioMode, onChange, onBrowse, onOpen }: {
  tracks: BgmTracks; audioMode: boolean
  onChange: (scope: BgmScope, patch: Partial<BgmTrack>) => void
  onBrowse: (scope: BgmScope, file: boolean) => void
  onOpen: (path: string) => void
}) {
  const [visible, setVisible] = useState({ hook: tracks.hook.enabled || !!tracks.hook.path, body: tracks.body.enabled || !!tracks.body.path })
  return <div className="space-y-1.5" aria-label="三轨配乐">
    {(['full', 'hook', 'body'] as const).map(scope => {
      if (scope !== 'full' && !visible[scope]) return null
      const track = tracks[scope], label = BGM_LABELS[scope]
      const disabled = !track.enabled || !track.path.trim()
      const master = scope === 'full' && audioMode
      const numberInput = (field: 'volume' | 'fade_in' | 'fade_out' | 'overlap', title: string, max: number, suffix: string) =>
        <label className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground">
          {title}<input type="number" aria-label={`${label} ${title}`} min="0" max={max} step={field === 'volume' ? 1 : .1}
            value={track[field]} disabled={disabled || (field === 'overlap' && (master || track.source_mode !== 'random'))}
            onChange={e => onChange(scope, { [field]: e.target.value })}
            className="h-6 w-14 rounded border border-border/15 bg-background-elev px-1.5 text-foreground outline-none focus:border-accent disabled:opacity-40" />{suffix}
        </label>
      return <div key={scope} className="rounded-[6px] border border-border/10 pb-1">
        <AssetCard kind="bgm" label={label} inputLabel={`${label} 路径`} value={track.path} pickAction="目录"
          onChange={path => onChange(scope, { path, enabled: true })}
          onBrowse={() => onBrowse(scope, false)} secondaryAction="文件" onSecondaryAction={() => onBrowse(scope, true)}
          onOpen={() => onOpen(track.path)} onClear={() => onChange(scope, { path: '' })} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-2 pt-1">
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-[10px] text-foreground">
            <Checkbox aria-label={`启用${label}`} checked={track.enabled} onCheckedChange={v => onChange(scope, { enabled: Boolean(v) })} />启用
          </label>
          {numberInput('volume', '音量', 200, '%')}
          <span className="text-[9px] text-muted-foreground">{scope === 'full' ? '覆盖全片' : scope === 'hook' ? '仅首段' : '从后段开始'}</span>
          {scope !== 'full' && <button type="button" aria-label={`收起${label}`} className="ml-auto text-[10px] text-muted-foreground hover:text-accent"
            onClick={() => { onChange(scope, { enabled: false }); setVisible(v => ({ ...v, [scope]: false })) }}>移除</button>}
        </div>
        <details className="px-2 pt-1 text-[10px]">
          <summary className="w-fit cursor-pointer text-accent focus-visible:outline focus-visible:outline-accent">{label} 设置</summary>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
            {numberInput('fade_in', '渐入', 3600, '秒')}{numberInput('fade_out', '渐出', 3600, '秒')}
            <label className="inline-flex items-center gap-1.5 text-muted-foreground">音乐不足
              <select aria-label={`${label} 音乐不足`} value={track.short_behavior} disabled={disabled || master}
                onChange={e => onChange(scope, { short_behavior: e.target.value as 'loop' | 'stop' })}
                className="h-6 rounded border border-border/15 bg-background-elev px-1 text-foreground disabled:opacity-40">
                <option value="loop">循环补齐</option><option value="stop">播完停止</option>
              </select>
            </label>
            <label className="inline-flex items-center gap-1.5 text-muted-foreground">播放起点
              <select aria-label={`${label} 播放起点`} value={master ? 'start' : track.source_mode} disabled={disabled || master}
                onChange={e => onChange(scope, { source_mode: e.target.value as 'start' | 'random' })}
                className="h-6 rounded border border-border/15 bg-background-elev px-1 text-foreground disabled:opacity-40">
                <option value="start">音乐开头</option><option value="random">随机裁切点</option>
              </select>
            </label>
            {numberInput('overlap', '裁切重叠率', 1, '')}
          </div>
          <p className="pb-1 text-[9px] leading-4 text-muted-foreground">{master ? '按完整全片音乐决定时长，从头播放一次；Hook 更长时保留 Hook。' : '长音乐裁尾，短音乐按上方策略处理。淡化作用于整段，超长自动缩短。'}</p>
        </details>
      </div>
    })}
    <div className="flex gap-2 px-1">
      {(['hook', 'body'] as const).filter(scope => !visible[scope]).map(scope => <button key={scope} type="button"
        className="rounded border border-border/15 px-2 py-1 text-[10px] text-muted-foreground hover:border-accent hover:text-accent"
        onClick={() => { setVisible(v => ({ ...v, [scope]: true })); onChange(scope, { enabled: true }) }}>＋{scope === 'hook' ? 'Hook' : 'Body'} 配乐</button>)}
      <span className="self-center text-[9px] text-muted-foreground">三轨可同时叠加，原声与配音独立</span>
    </div>
  </div>
}
