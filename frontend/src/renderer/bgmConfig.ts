import type { BgmTrack, BgmTracks } from './api/client'

export function defaultBgmTrack(enabled = true): BgmTrack {
  return { enabled, path: '', volume: 30, fade_in: 0, fade_out: 0,
    short_behavior: 'loop', source_mode: 'start', overlap: .3 }
}

export function migrateBgmConfig(saved: any): { tracks: BgmTracks; durationMode: 'clips' | 'bgm'; notice: string | null } {
  const defaults = { full: defaultBgmTrack(), hook: defaultBgmTrack(false), body: defaultBgmTrack(false) }
  if (saved.bgm_tracks) {
    const tracks = Object.fromEntries(Object.entries(defaults).map(([scope, track]) =>
      [scope, { ...track, ...saved.bgm_tracks[scope] }])) as BgmTracks
    return { tracks, durationMode: tracks.full.enabled && tracks.full.path.trim() && saved.duration_mode === 'bgm' ? 'bgm' : 'clips', notice: null }
  }
  const path = String(saved.bgm_dir || '').trim()
  const bodyOnly = saved.apply_bgm_to_hook === false
  const tracks = defaults
  if (path) tracks[bodyOnly ? 'body' : 'full'] = {
    ...defaultBgmTrack(), path, volume: saved.vol_bgm ?? 30,
    overlap: saved.bgm_r ?? .3, source_mode: 'random',
  }
  const notice = path && bodyOnly && saved.duration_mode === 'bgm'
    ? '旧版 Body 音乐与音量已保留，已恢复按片段数量。需要按音乐时长生成时，请选择全片 BGM。' : null
  return { tracks, durationMode: path && !bodyOnly && saved.duration_mode === 'bgm' ? 'bgm' : 'clips', notice }
}
