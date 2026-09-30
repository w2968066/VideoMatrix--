const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const Module = require('node:module')
const esbuild = require('esbuild')
const fs = require('node:fs')
const os = require('node:os')

function load(source) {
  const filename = path.resolve(__dirname, '../', source)
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module)
  mod.paths = Module._nodeModulePaths(path.dirname(filename))
  mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}

test('directory browser shows files, folders and empty directories without selecting a file', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-browser-'))
  try {
    fs.mkdirSync(path.join(root, 'Empty'))
    fs.mkdirSync(path.join(root, '素材目录'))
    fs.writeFileSync(path.join(root, 'video.mp4'), '')
    fs.writeFileSync(path.join(root, '.hidden.txt'), '')
    const { listDirectory } = load('src/main/directoryBrowser.ts')
    const result = await listDirectory(root)
    assert.equal(result.path, root)
    assert.deepEqual(result.entries.map(row => row.directory), [true, true, false, false])
    assert(result.entries.some(row => row.name === 'video.mp4' && !row.directory))
    assert(result.entries.some(row => row.name === '.hidden.txt'))
    const media = result.entries.find(row => row.name === 'video.mp4')
    assert.equal(media.size, 0)
    assert.equal(media.modifiedAt, fs.statSync(media.path).mtimeMs)
    assert.equal(result.entries.find(row => row.name === 'Empty').size, null)
    assert.equal((await listDirectory(path.join(root, 'Empty'))).entries.length, 0)
    await assert.rejects(listDirectory(path.join(root, 'missing')), { code: 'ENOENT' })
    await assert.rejects(listDirectory(path.join(root, 'video.mp4')), { code: 'ENOTDIR' })
  } finally { fs.rmSync(root, { recursive: true }) }
})

test('new folders are created only under the selected parent, never overwrite existing items', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-create-folder-'))
  try {
    const { createDirectory, listDirectory } = load('src/main/directoryBrowser.ts')
    const result = await createDirectory(root, '测试产出')
    assert.equal(result, path.join(root, '测试产出'))
    assert(fs.statSync(result).isDirectory())
    assert((await listDirectory(root)).entries.some(entry => entry.name === '测试产出' && entry.directory))
    await assert.rejects(createDirectory(root, '测试产出'), { code: 'EEXIST' })
    fs.writeFileSync(path.join(root, 'existing.txt'), 'keep')
    await assert.rejects(createDirectory(root, 'existing.txt'), { code: 'EEXIST' })
    assert.equal(fs.readFileSync(path.join(root, 'existing.txt'), 'utf8'), 'keep')
    for (const name of ['', '.', '..', '../outside', 'a/b', 'a\\b', 'C:\\outside', 'bad*name', 'CON', 'nul.txt', 'name.', 'name ', '\0']) {
      await assert.rejects(createDirectory(root, name), /名称无效/)
    }
    await assert.rejects(createDirectory('relative', 'test'), /路径无效/)
    await assert.rejects(createDirectory(root, 'x'.repeat(256)), /太长/)
    await assert.rejects(createDirectory(path.join(root, 'missing'), 'test'), { code: 'ENOENT' })
    assert.deepEqual(fs.readdirSync(root).sort(), ['existing.txt', '测试产出'].sort())
  } finally { fs.rmSync(root, { recursive: true }) }
})

test('file management validates exact children, protects directories and never falls back from trash failures', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-management-'))
  try {
    const { createDirectoryActions } = load('src/main/directoryActions.ts')
    const files = ['a.mp4', 'b.mp4'].map(name => path.join(root, name))
    for (const file of files) fs.writeFileSync(file, 'keep')
    const protectedDir = path.join(root, 'protected')
    fs.mkdirSync(protectedDir)
    let active = false, confirmations = 0, trashCalls = 0, confirm = true
    const actions = createDirectoryActions({ protectedPaths: [protectedDir],
      assertIdle: async () => { if (active) throw Error('任务运行中') },
      confirmTrash: async paths => { confirmations += 1; assert.equal(paths.length, 2); return confirm },
      trashItem: async () => { trashCalls += 1; throw Error('系统回收站不可用') } })
    await assert.rejects(actions.trash(root, [files[0], path.join(root, '..', 'outside')]), /当前目录/)
    await assert.rejects(actions.trash(root, [root]), /当前目录/)
    await assert.rejects(actions.trash(root, [protectedDir]), /不能删除/)
    assert.equal((await actions.targets(root, [protectedDir])).length, 1, 'Read-only reveal/copy are allowed')
    assert.equal(confirmations, 0)
    active = true
    await assert.rejects(actions.trash(root, files), /任务运行中/)
    await assert.rejects(actions.rename(root, files[0], 'renamed'), /任务运行中/)
    assert.equal(confirmations, 0)
    active = false; confirm = false
    assert.equal((await actions.trash(root, files)).cancelled, true)
    assert.equal(trashCalls, 0)
    confirm = true
    const result = await actions.trash(root, files)
    assert.equal(result.removed.length, 0)
    assert.equal(result.failed.length, 2)
    assert.equal(trashCalls, 2)
    for (const file of files) assert.equal(fs.readFileSync(file, 'utf8'), 'keep')
    const activeAfterConfirm = createDirectoryActions({ protectedPaths: [],
      assertIdle: async () => { if (active) throw Error('任务运行中') },
      confirmTrash: async () => { active = true; return true },
      trashItem: async () => { throw Error('must not be called') } })
    await assert.rejects(activeAfterConfirm.trash(root, files), /任务运行中/)
    for (const file of files) assert(fs.existsSync(file))
  } finally { fs.rmSync(root, { recursive: true }) }
})

test('native rename preserves extensions and refuses existing destinations for files and folders', { skip: !['win32', 'darwin'].includes(process.platform) }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-rename-'))
  try {
    const { createDirectoryActions } = load('src/main/directoryActions.ts')
    const { renameWithoutReplace } = load('src/main/renameWithoutReplace.ts')
    const actions = createDirectoryActions({ protectedPaths: [], assertIdle: async () => {}, confirmTrash: async () => false, trashItem: async () => {} })
    const file = path.join(root, '素材.mp4'), other = path.join(root, '已有.mp4')
    fs.writeFileSync(file, 'original'); fs.writeFileSync(other, 'existing')
    await assert.rejects(actions.rename(root, file, '已有'), /不会覆盖/)
    await assert.rejects(actions.rename(root, file, '../outside'), /名称无效/)
    await assert.rejects(renameWithoutReplace(file, other))
    assert.equal(fs.readFileSync(other, 'utf8'), 'existing')
    const changed = await actions.rename(root, file, '新素材')
    assert.equal(changed, path.join(root, '新素材.mp4'))
    assert.equal(fs.readFileSync(changed, 'utf8'), 'original')
    fs.mkdirSync(path.join(root, 'Folder')); fs.mkdirSync(path.join(root, 'ExistingFolder'))
    await assert.rejects(renameWithoutReplace(path.join(root, 'Folder'), path.join(root, 'ExistingFolder')))
    assert(fs.statSync(path.join(root, 'Folder')).isDirectory())
    const next = await actions.rename(root, path.join(root, 'Folder'), 'RenamedFolder')
    assert(fs.statSync(next).isDirectory())
  } finally { fs.rmSync(root, { recursive: true }) }
})

test('rename and trash remap asset references and descendants without changing sibling prefixes', () => {
  const { remapPath, remapConfiguredPaths } = load('src/renderer/directoryPathUpdates.ts')
  const changes = [{ from: 'C:\\assets\\Old', to: 'C:\\assets\\New' }]
  assert.equal(remapPath('c:\\assets\\old\\child', changes), 'C:\\assets\\New\\child')
  assert.equal(remapPath('C:\\assets\\Older', changes), 'C:\\assets\\Older')
  const config = { hook_dir: 'C:\\assets\\Old', body_dirs: ['C:\\assets\\Old\\child', 'C:\\assets\\Other'],
    body_groups: [{ enabled: true, folder: 'C:\\assets\\Old', clip_count: 2 }],
    bgm_tracks: { full: { path: 'C:\\assets\\Old\\music.wav', enabled: true } }, bgm_dir: '', base_out_dir: 'D:\\output' }
  const renamed = remapConfiguredPaths(config, changes)
  assert.equal(renamed.hook_dir, 'C:\\assets\\New')
  assert.equal(renamed.bgm_tracks.full.path, 'C:\\assets\\New\\music.wav')
  const removed = remapConfiguredPaths(config, [{ from: 'C:\\assets\\Old', to: null }])
  assert.equal(removed.hook_dir, '')
  assert.deepEqual(removed.body_dirs, ['C:\\assets\\Other'])
  assert.equal(removed.body_groups[0].folder, '')
  assert.equal(removed.bgm_tracks.full.path, '')
  assert.equal(removed.base_out_dir, undefined)
})

test('directory view sorts naturally with folders first and unknown metadata last', () => {
  const { sortDirectoryEntries, formatFileSize, formatModifiedAt } = load('src/renderer/directoryView.ts')
  const rows = [
    { name: 'video10.mp4', directory: false, size: 2048, modifiedAt: 2000 },
    { name: 'video2.mp4', directory: false, size: 0, modifiedAt: 1000 },
    { name: 'unknown.mp4', directory: false, size: null, modifiedAt: null },
    { name: 'folder', directory: true, size: null, modifiedAt: 500 },
  ]
  assert.deepEqual(sortDirectoryEntries(rows, 'name', false).map(r => r.name), ['folder', 'unknown.mp4', 'video2.mp4', 'video10.mp4'])
  assert.deepEqual(sortDirectoryEntries(rows, 'modifiedAt', true).map(r => r.name), ['folder', 'video10.mp4', 'video2.mp4', 'unknown.mp4'])
  assert.deepEqual(sortDirectoryEntries(rows, 'modifiedAt', false).map(r => r.name), ['folder', 'video2.mp4', 'video10.mp4', 'unknown.mp4'])
  assert.equal(sortDirectoryEntries(rows, 'size', true)[1].name, 'video10.mp4')
  assert.equal(rows[0].name, 'video10.mp4', 'Sorting must not mutate the original listing')
  assert.equal(formatFileSize(0), '0 B')
  assert.equal(formatFileSize(2048), '2.0 KB')
  assert.equal(formatFileSize(null), '—')
  assert.equal(formatModifiedAt(null), '—')
})

test('browser favorites, view and sorting persist independently of mixing config', () => {
  let saved
  global.localStorage = { getItem: () => saved, setItem: (key, value) => { assert.equal(key, 'vm-directory-browser'); saved = value } }
  const { readBrowserPreferences, writeBrowserPreferences, favoriteName } = load('src/renderer/directoryView.ts')
  assert.equal(readBrowserPreferences().view, 'thumbnails')
  writeBrowserPreferences({ view: 'details', sort: 'modifiedAt', descending: true, favorites: ['C:\\素材', 'D:\\音乐'] })
  assert.deepEqual(readBrowserPreferences().favorites, ['C:\\素材', 'D:\\音乐'])
  assert.equal(readBrowserPreferences().view, 'details')
  assert.equal(readBrowserPreferences().descending, true)
  assert.equal(favoriteName('D:\\音乐'), '音乐')
  saved = '{invalid'
  assert.equal(readBrowserPreferences().sort, 'name')
  saved = JSON.stringify({ view: 'invalid', favorites: [null, '', 'a', 'a'] })
  assert.deepEqual(readBrowserPreferences().favorites, ['a'])
  global.localStorage = { getItem: () => { throw Error('blocked') }, setItem: () => { throw Error('blocked') } }
  assert.equal(readBrowserPreferences().view, 'thumbnails')
  assert.doesNotThrow(() => writeBrowserPreferences(readBrowserPreferences()))
})

test('native thumbnails deduplicate, cache, invalidate and limit concurrent OS requests', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-thumbnails-'))
  try {
    const { createThumbnailReader } = load('src/main/nativeThumbnails.ts')
    let active = 0, peak = 0, calls = 0
    const read = createThumbnailReader(async () => {
      calls += 1; active += 1; peak = Math.max(peak, active)
      await new Promise(resolve => setTimeout(resolve, 10))
      active -= 1
      return 'data:image/png;base64,test'
    })
    const files = Array.from({ length: 6 }, (_, i) => path.join(root, `${i}.mp4`))
    for (const file of files) fs.writeFileSync(file, 'fixture')
    await Promise.all([read(files[0]), read(files[0]), ...files.map(read)])
    assert.equal(calls, 6)
    assert.equal(peak, 2)
    await read(files[0]); assert.equal(calls, 6)
    fs.appendFileSync(files[0], 'changed')
    await read(files[0]); assert.equal(calls, 7)
    assert.equal(await read('relative.mp4'), null)
    assert.equal(await read(path.join(root, 'missing.mp4')), null)
    assert.equal(await read(path.join(root, 'text.txt')), null)
    const failure = createThumbnailReader(async () => { throw Error('unsupported codec') })
    assert.equal(await failure(files[0]), null)
    assert.equal(await read(files[1]), 'data:image/png;base64,test')
  } finally { fs.rmSync(root, { recursive: true }) }
})

test('three-track migration preserves legacy audio and makes Body-duration conversion explicit', () => {
  const { migrateBgmConfig } = load('src/renderer/bgmConfig.ts')
  const body = migrateBgmConfig({ bgm_dir: 'body music', vol_bgm: 45, bgm_r: .7,
    apply_bgm_to_hook: false, duration_mode: 'bgm' })
  assert.equal(body.tracks.full.path, '')
  assert.equal(body.tracks.body.path, 'body music')
  assert.equal(body.tracks.body.volume, 45)
  assert.equal(body.tracks.body.overlap, .7)
  assert.equal(body.durationMode, 'clips')
  assert.match(body.notice, /全片 BGM/)
  const full = migrateBgmConfig({ bgm_dir: 'full music', apply_bgm_to_hook: true, duration_mode: 'bgm' })
  assert.equal(full.durationMode, 'bgm')
  assert.equal(full.notice, null)
  assert.equal(migrateBgmConfig({ bgm_tracks: body.tracks, duration_mode: 'clips' }).notice, null)
})

test('three-track settings normalize numeric values and disabled full track cancels master duration', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const { normalizeConfigForRequest } = load('src/renderer/api/client.ts')
  let config = useStore.getState().config
  const tracks = { ...config.bgm_tracks,
    full: { ...config.bgm_tracks.full, path: ' full.wav ', volume: '0' },
    hook: { ...config.bgm_tracks.hook, enabled: true, path: 'hook.wav', fade_in: '0.5', fade_out: '1' } }
  useStore.getState().setConfig({ bgm_tracks: tracks, duration_mode: 'bgm', finished_hook: true })
  config = normalizeConfigForRequest(useStore.getState().config)
  assert.equal(config.bgm_dir, 'full.wav')
  assert.equal(config.bgm_tracks.full.volume, 0)
  assert.equal(config.bgm_tracks.hook.fade_in, .5)
  assert.equal(config.duration_mode, 'bgm')
  assert.equal(config.finished_hook, true)
  useStore.getState().setConfig({ bgm_tracks: { ...tracks, full: { ...tracks.full, enabled: false } } })
  assert.equal(useStore.getState().config.duration_mode, 'clips')
  assert.equal(useStore.getState().config.bgm_tracks.hook.path, 'hook.wav')
  config = normalizeConfigForRequest({ ...config, bgm_tracks: { ...config.bgm_tracks,
    body: { ...config.bgm_tracks.body, enabled: false, fade_in: '', overlap: '' } } })
  assert.equal(config.bgm_tracks.body.fade_in, 0)
  assert.equal(config.bgm_tracks.body.overlap, .3)
})

test('BGM removal restores clip mode while preserving manual and audio settings', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  useStore.getState().setConfig({ bgm_dir: 'music', duration_mode: 'bgm', total_clips: 7, vol_bgm: 45 })
  assert.equal(useStore.getState().config.duration_mode, 'bgm')
  useStore.getState().setConfig({ bgm_dir: '  ' })
  assert.equal(useStore.getState().config.duration_mode, 'clips')
  assert.equal(useStore.getState().config.total_clips, 7)
  assert.equal(useStore.getState().config.vol_bgm, 45)
  useStore.getState().setConfig({ bgm_dir: 'new music' })
  assert.equal(useStore.getState().config.duration_mode, 'clips')
})

test('output defaults are 1080p 8000k 30fps GPU enabled, saved choices remain intact', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  let config = load('src/renderer/store.ts').useStore.getState().config
  assert.equal(config.resolution, '1080*1920')
  assert.equal(config.bitrate, '8000k')
  assert.equal(config.fps, '30')
  assert.equal(config.enable_gpu, true)
  global.localStorage = { getItem: () => JSON.stringify({ bitrate: '12000k', fps: '24', enable_gpu: false }), setItem: () => {} }
  config = load('src/renderer/store.ts').useStore.getState().config
  assert.equal(config.bitrate, '12000k')
  assert.equal(config.fps, '24')
  assert.equal(config.enable_gpu, false)
})

test('full Body flags survive request normalization and ignore disabled duration values', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const { normalizeConfigForRequest } = load('src/renderer/api/client.ts')
  const base = useStore.getState().config
  const normal = normalizeConfigForRequest({ ...base, body_full_duration: true, t_body: '', body_r: '' })
  assert.equal(normal.body_full_duration, true)
  assert.equal(normal.t_body, 3)
  assert.equal(normal.body_r, 0)
  const grouped = normalizeConfigForRequest({ ...base, body_mode: 'grouped', body_groups: [
    { enabled: true, folder: 'a', clip_count: 2, clip_duration: '', full_duration: true },
    { enabled: true, folder: 'b', clip_count: 1, clip_duration: 4, full_duration: false },
  ] })
  assert.deepEqual(grouped.body_groups.map(g => g.full_duration), [true, false])
  assert.equal(grouped.body_groups[1].clip_duration, 4)
})

test('startup rejects old versions, unrelated instances and invalid ports', () => {
  const { validateIdentity } = load('src/main/backendHandshake.ts')
  assert.doesNotThrow(() => validateIdentity({ port: 53001, version: '2.3.2', instance: 'owned' }, '2.3.2', 'owned'))
  for (const response of [
    { status: 'ok' },
    { port: 53001, version: '2.3.1', instance: 'owned' },
    { port: 53001, version: '2.3.2', instance: 'another-process' },
    { port: 0, version: '2.3.2', instance: 'owned' },
  ]) assert.throws(() => validateIdentity(response, '2.3.2', 'owned'))
})

test('real store shows backend logs once, retains cursor after clear and captures final logs', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const task = { task_id: 'a', status: 'running', log_lines: ['[加速] NVIDIA'] }
  useStore.getState().setTasks([task])
  useStore.getState().setTasks([task])
  assert.deepEqual(useStore.getState().logs, ['[加速] NVIDIA'])
  useStore.getState().clearLogs()
  useStore.getState().setTasks([task])
  assert.deepEqual(useStore.getState().logs, [])
  useStore.getState().setTasks([{ ...task, status: 'completed', log_lines: [...task.log_lines, '[加速降级] CPU'] }])
  assert.deepEqual(useStore.getState().logs, ['[加速降级] CPU'])
})
