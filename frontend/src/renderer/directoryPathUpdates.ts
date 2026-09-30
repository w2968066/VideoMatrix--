import type { VideoConfig } from './api/client'

export type PathChange = { from: string; to: string | null }
export function remapPath(value: string, changes: PathChange[]) {
  for (const change of changes) {
    const source = change.from.replace(/[\\/]+$/, '')
    const windows = /^[a-z]:[\\/]/i.test(source) || source.startsWith('\\\\')
    const comparable = (p: string) => windows ? p.replace(/\//g, '\\').toLowerCase() : p
    const candidate = comparable(value)
    const original = comparable(source)
    if (candidate === original || candidate.startsWith(original + (windows ? '\\' : '/'))) {
      return change.to === null ? '' : change.to + value.slice(source.length)
    }
  }
  return value
}
export function remapConfiguredPaths(config: VideoConfig, changes: PathChange[]): Partial<VideoConfig> {
  const patch: Partial<VideoConfig> = {}
  for (const field of ['hook_dir', 'bgm_dir', 'voice_dir', 'srt_dir', 'watermark_path', 'base_out_dir'] as const) {
    if (typeof config[field] !== 'string') continue
    const next = remapPath(config[field]!, changes)
    if (next !== config[field]) patch[field] = next
  }
  const bodyDirs = config.body_dirs.map(value => remapPath(value, changes)).filter(Boolean)
  if (bodyDirs.join('\0') !== config.body_dirs.join('\0')) patch.body_dirs = bodyDirs
  const groups = config.body_groups.map(group => ({ ...group, folder: remapPath(group.folder, changes) }))
  if (groups.some((group, i) => group.folder !== config.body_groups[i].folder)) patch.body_groups = groups
  if (config.bgm_tracks && Object.values(config.bgm_tracks).some(track => remapPath(track.path, changes) !== track.path))
    patch.bgm_tracks = Object.fromEntries(Object.entries(config.bgm_tracks).map(([key, track]) => [key, { ...track, path: remapPath(track.path, changes) }])) as VideoConfig['bgm_tracks']
  return patch
}
