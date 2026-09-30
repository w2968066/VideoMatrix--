// Close the idle dev app first. Uses an isolated profile and generated test materials only.
const { _electron: electron } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const assert = require('node:assert/strict')

async function main() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'videomatrix-desktop-management-'))
  const materials = path.join(temporary, 'materials')
  fs.mkdirSync(materials)
  const originalFile = path.join(materials, '测试原素材.txt')
  fs.writeFileSync(originalFile, 'generated fixture, not a user material')
  let app
  try {
    const env = { ...process.env, NODE_ENV: 'development', VIDEOMATRIX_PYTHON: path.resolve(__dirname, '../../.build-venv/Scripts/python.exe') }
    delete env.ELECTRON_RUN_AS_NODE
    app = await electron.launch({ executablePath: path.resolve(__dirname, '../node_modules/electron/dist/electron.exe'),
      cwd: path.resolve(__dirname, '..'), args: [`--user-data-dir=${path.join(temporary, 'profile')}`, '.'], env })
    await app.firstWindow()
    await app.evaluate(({ BrowserWindow }) => { for (const window of BrowserWindow.getAllWindows()) window.webContents.closeDevTools() })
    const page = app.windows().find(window => !window.url().startsWith('devtools:')) || await app.waitForEvent('window')
    console.log('Desktop test renderer:', page.url())
    await page.waitForFunction(() => !!window.electronAPI)
    await app.evaluate(({ dialog, clipboard, shell, Menu }) => {
      global.vmDirectoryTest = { response: 0, confirmations: [], copied: '', opened: [], revealed: [] }
      dialog.showMessageBox = async (...args) => {
        const options = args.find(value => value && value.buttons)
        global.vmDirectoryTest.confirmations.push(options)
        return { response: global.vmDirectoryTest.response, checkboxChecked: false }
      }
      clipboard.writeText = text => { global.vmDirectoryTest.copied = text }
      shell.openPath = async file => { global.vmDirectoryTest.opened.push(file); return '' }
      shell.showItemInFolder = file => { global.vmDirectoryTest.revealed.push(file) }
      Menu.prototype.popup = function(options) {
        this.items.find(item => item.label === '复制完整路径').click()
        options.callback()
      }
    })
    const folder = await page.evaluate(async parent => window.electronAPI.createDirectory(parent, '测试文件夹'), materials)
    assert(fs.statSync(folder).isDirectory())
    const renamed = await page.evaluate(async ({ parent, file }) => window.electronAPI.renameEntry(parent, file, '测试改名'), { parent: materials, file: originalFile })
    assert.equal(renamed, path.join(materials, '测试改名.txt'))
    assert.equal(fs.readFileSync(renamed, 'utf8'), 'generated fixture, not a user material')
    assert.equal(await page.evaluate(async ({ parent, file }) => window.electronAPI.directoryActionMenu(parent, [file]), { parent: materials, file: renamed }), 'copy')
    await page.evaluate(async ({ parent, file }) => {
      await window.electronAPI.copyEntryPaths(parent, [file])
      await window.electronAPI.openEntry(parent, file)
      await window.electronAPI.revealEntry(parent, file)
    }, { parent: materials, file: renamed })
    const readOnly = await app.evaluate(() => global.vmDirectoryTest)
    assert.equal(readOnly.copied, renamed)
    assert.deepEqual(readOnly.opened, [renamed])
    assert.deepEqual(readOnly.revealed, [renamed])
    const cancelled = await page.evaluate(async ({ parent, file }) => window.electronAPI.trashEntries(parent, [file]), { parent: materials, file: renamed })
    assert.equal(cancelled.cancelled, true)
    assert(fs.existsSync(renamed))
    await app.evaluate(() => {
      global.vmDirectoryOriginalFetch = global.fetch
      global.fetch = async (url, options) => String(url).endsWith('/api/tasks')
        ? new Response(JSON.stringify([{ status: 'running' }]), { headers: { 'content-type': 'application/json' } })
        : global.vmDirectoryOriginalFetch(url, options)
      global.vmDirectoryTest.response = 1
    })
    const activeError = await page.evaluate(async ({ parent, file }) => {
      try { await window.electronAPI.trashEntries(parent, [file]); return '' } catch (error) { return error.message }
    }, { parent: materials, file: renamed })
    assert.match(activeError, /混剪任务正在运行/)
    assert(fs.existsSync(renamed))
    await app.evaluate(() => { global.fetch = global.vmDirectoryOriginalFetch })
    // Actual OS trash, but only the two exact test paths generated above.
    const removed = await page.evaluate(async ({ parent, items }) => window.electronAPI.trashEntries(parent, items), { parent: materials, items: [renamed, folder] })
    assert.deepEqual(removed.removed, [renamed, folder])
    assert.deepEqual(removed.failed, [])
    assert(!fs.existsSync(renamed)); assert(!fs.existsSync(folder))
    const confirmations = await app.evaluate(() => global.vmDirectoryTest.confirmations)
    assert.match(confirmations.at(-1).message, /1 个文件、1 个文件夹/)
    assert(confirmations.at(-1).detail.includes(renamed))
    assert(confirmations.at(-1).detail.includes(folder))
    console.log('Real desktop IPC passed: create, native rename, menu, safe open/reveal/copy, cancellation, active-task blocking, actual OS trash of generated fixtures.')
  } finally {
    await app?.close()
    if (path.dirname(temporary) === path.resolve(os.tmpdir()) && path.basename(temporary).startsWith('videomatrix-desktop-management-')) fs.rmSync(temporary, { recursive: true, force: true })
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
