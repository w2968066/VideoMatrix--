const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const Module = require('node:module')
const esbuild = require('esbuild')

function load(source) {
  const filename = path.resolve(__dirname, '../', source)
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module)
  mod.paths = Module._nodeModulePaths(path.dirname(filename))
  mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}

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
