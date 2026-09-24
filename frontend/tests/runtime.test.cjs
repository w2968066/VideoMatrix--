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
