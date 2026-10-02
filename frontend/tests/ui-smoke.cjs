// Run after npm run build; requires Playwright (local install or NODE_PATH).
const { chromium } = require('playwright')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

async function main() {
  const root = path.resolve(__dirname, '../dist/renderer')
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]))
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(fs.readFileSync(file))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } })
    const errors = []
    const taskLogs = []
    let taskFixtures = null
    const taskStopCalls = []
    const taskGetCalls = []
    let pendingStopRoute = null
    let failNextStop = false
    const benchmarkCalls = []
    let pendingBenchmarkRoute = null
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', route => {
      const url = route.request().url()
      if (new URL(url).pathname === '/api/benchmark' && route.request().method() === 'POST') {
        benchmarkCalls.push(route.request().postDataJSON())
        taskFixtures = [{ task_id: `benchmark-ui-${benchmarkCalls.length}`, task_name: `智能压测回归 ${benchmarkCalls.length}`,
          status: 'running', log_lines: [], output_files: [], progress: 17, current: 1, total: 6,
          message: '测试服务端真实进度 17%' }]
        pendingBenchmarkRoute = route
        return
      }
      const taskStopMatch = new URL(url).pathname.match(/^\/api\/tasks\/([^/]+)\/stop$/)
      if (taskStopMatch && route.request().method() === 'POST') {
        taskStopCalls.push(taskStopMatch[1])
        if (failNextStop) {
          failNextStop = false
          return route.fulfill({ status: 500, json: { detail: '测试停止失败' } })
        }
        pendingStopRoute = route
        return
      }
      const taskGetMatch = new URL(url).pathname.match(/^\/api\/tasks\/([^/]+)$/)
      if (taskGetMatch && route.request().method() === 'GET' && taskFixtures) {
        taskGetCalls.push(taskGetMatch[1])
        return route.fulfill({ json: taskFixtures.find(task => task.task_id === taskGetMatch[1]) })
      }
      const body = url.includes('/health') ? { status: 'ok' } : url.includes('/tasks') ? (taskFixtures ?? (taskLogs.length ? [{ task_id: 'scroll-test', task_name: 'Scroll', status: 'running', log_lines: taskLogs, output_files: [], progress: 0, current: 0, total: 1 }] : []))
        : url.includes('/scan') ? { files: ['music.wav'], count: 1 }
        : url.includes('/probe') ? { audio_duration: 25, source_duration: 3 } : {}
      return route.fulfill({ json: body })
    })
    await page.goto(`http://127.0.0.1:${server.address().port}`)
    const nativeResultsPath = path.resolve(__dirname, '../dist/native-thumbnail-results.json')
    const nativeResults = fs.existsSync(nativeResultsPath) ? JSON.parse(fs.readFileSync(nativeResultsPath, 'utf8')) : null
    await page.evaluate(nativeResults => {
      window.thumbnailCalls = []
      const createdFolders = new Map()
      const renamedFolders = new Map()
      const removedFiles = new Set()
      window.directoryOperationCalls = []
      window.menuAction = null
      window.trashMode = 'cancel'
      window.electronAPI = { getBackendPort: async () => 8765,
        directoryActionMenu: async (parent, items) => { window.directoryOperationCalls.push(['menu', items]); return window.menuAction },
        openEntry: async (parent, item) => { window.directoryOperationCalls.push(['open', item]) },
        revealEntry: async (parent, item) => { window.directoryOperationCalls.push(['reveal', item]) },
        copyEntryPaths: async (parent, items) => { window.directoryOperationCalls.push(['copy', items]) },
        renameEntry: async (parent, item, name) => {
          if (name === '存在') throw Error('同名文件或文件夹已存在，不会覆盖。')
          const oldName = item.split('\\').pop()
          const directory = ['Group A', 'Group B', 'Empty', ...(createdFolders.get(parent) || [])].includes(oldName)
          const extension = directory || !oldName.includes('.') ? '' : '.' + oldName.split('.').pop()
          const next = `${parent}\\${name}${extension}`
          renamedFolders.set(item, next)
          window.directoryOperationCalls.push(['rename', item, next])
          return next
        },
        trashEntries: async (parent, items) => {
          window.directoryOperationCalls.push(['trash', items])
          if (window.trashMode === 'cancel') return { cancelled: true, removed: [], failed: [] }
          const removed = window.trashMode === 'partial' ? items.slice(0, 1) : items
          removed.forEach(file => removedFiles.add(file))
          return { cancelled: false, removed, failed: window.trashMode === 'partial' ? items.slice(1).map(path => ({ path, message: '回收站失败' })) : [] }
        },
        createDirectory: async (parent, name) => {
          if (name === '存在') throw new Error('同名文件或文件夹已存在，请换个名称。')
          if (name === '无权限') throw new Error('没有权限在当前目录创建文件夹，请选择其他目录。')
          if (/[\\/]/.test(name)) throw new Error('文件夹名称无效，请勿使用路径。')
          const names = createdFolders.get(parent) || []
          if (names.includes(name)) throw new Error('同名文件或文件夹已存在，请换个名称。')
          createdFolders.set(parent, [...names, name])
          return `${parent}\\${name}`
        },
        readThumbnail: async file => {
          window.thumbnailCalls.push(file)
          return nativeResults ? (file.endsWith('.png') ? nativeResults.image : nativeResults.video) : null
        },
        readDirectory: async directory => {
          const current = directory || 'C:\\assets'
          if (current === 'C:\\missing') throw new Error('文件夹不存在，请检查路径。')
          const baseNames = current === 'C:\\assets' ? ['Group A', 'Group B', 'Empty', 'video-preview.mp4', 'preview.png', 'subtitle.srt', ...Array.from({ length: 60 }, (_, i) => `extra-${i}.mp4`)]
            : current.endsWith('Empty') ? [] : ['inside-folder.mp4', 'music.wav']
          const addedNames = createdFolders.get(current) || []
          const names = [...baseNames, ...addedNames]
          return { path: current, parent: current === 'C:\\assets' ? 'C:\\' : 'C:\\assets', home: 'C:\\assets', roots: ['C:\\', 'D:\\'],
            entries: names.map((name, index) => {
              const nextPath = renamedFolders.get(`${current}\\${name}`) || `${current}\\${name}`
              return { name: nextPath.split('\\').pop(), path: nextPath, directory: ['Group A', 'Group B', 'Empty', ...addedNames].includes(name),
                modifiedAt: 1700000000000 + index * 60000, size: ['Group A', 'Group B', 'Empty', ...addedNames].includes(name) ? null : index * 1024 }
            }).filter(entry => !removedFiles.has(entry.path)) }
        }, openPath: async () => {}, openFile: async () => null }
    }, nativeResults)
    const hookBrowse = page.getByRole('textbox', { name: 'Hook 首段', exact: true }).locator('..').getByRole('button', { name: '浏览', exact: true })
    await hookBrowse.click()
    const picker = page.getByRole('dialog', { name: '选择文件夹', exact: true })
    await picker.getByText('video-preview.mp4', { exact: true }).waitFor()
    assert((await page.evaluate(() => window.thumbnailCalls)).length < 30, 'Offscreen files must not be eagerly loaded')
    if (nativeResults) {
      await picker.getByRole('img', { name: 'extra-0.mp4 缩略图', exact: true }).waitFor()
      await picker.getByText('video-preview.mp4', { exact: true }).scrollIntoViewIfNeeded()
      await picker.getByRole('img', { name: 'video-preview.mp4 缩略图', exact: true }).waitFor()
      await picker.getByText('preview.png', { exact: true }).scrollIntoViewIfNeeded()
      await picker.getByRole('img', { name: 'preview.png 缩略图', exact: true }).waitFor()
    }
    assert(!(await page.evaluate(() => window.thumbnailCalls)).some(file => file.endsWith('.srt')))
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-thumbnails-dark.png') })
    await picker.locator('.directory-picker-list').evaluate(el => { el.scrollTop = el.scrollHeight })
    await page.waitForFunction(() => window.thumbnailCalls.some(file => file.endsWith('extra-59.mp4')))
    await picker.getByRole('button', { name: '列表', exact: true }).click()
    assert.equal(await picker.locator('.directory-view-list').count(), 1)
    assert.equal(await picker.locator('.directory-thumbnail img').count(), 0, 'List mode uses small icons')
    await picker.getByRole('button', { name: '收藏当前目录', exact: true }).click()
    assert.equal(await picker.getByRole('button', { name: '打开收藏 C:\\assets', exact: true }).count(), 1)
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-list.png') })
    await picker.getByRole('button', { name: '详细信息', exact: true }).click()
    const details = picker.getByRole('table', { name: '文件夹详细信息', exact: true })
    assert.equal(await details.getByRole('columnheader').count(), 5)
    await picker.getByRole('combobox', { name: '排序方式', exact: true }).selectOption('modifiedAt')
    let names = await details.locator('tbody .directory-entry-name').allTextContents()
    assert.deepEqual(names.slice(0, 4), ['Empty', 'Group B', 'Group A', 'extra-59.mp4'])
    await picker.getByRole('button', { name: '切换排序方向', exact: true }).click()
    names = await details.locator('tbody .directory-entry-name').allTextContents()
    assert.deepEqual(names.slice(0, 4), ['Group A', 'Group B', 'Empty', 'video-preview.mp4'])
    await details.getByRole('button', { name: '按名称排序', exact: true }).click()
    assert.equal(await details.getByRole('columnheader').first().getAttribute('aria-sort'), 'ascending')
    names = await details.locator('tbody .directory-entry-name').allTextContents()
    assert(names.indexOf('extra-2.mp4') < names.indexOf('extra-10.mp4'))
    assert.equal(await details.getByText('3.0 KB', { exact: true }).count(), 1)
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-details-dark.png') })
    await picker.getByRole('button', { name: '取消', exact: true }).click()
    await hookBrowse.click()
    await details.waitFor()
    assert.equal(await picker.getByRole('combobox', { name: '排序方式', exact: true }).inputValue(), 'name')
    assert.equal(await picker.getByRole('button', { name: '打开收藏 C:\\assets', exact: true }).count(), 1)
    await picker.getByRole('button', { name: 'Group A', exact: true }).dblclick()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '后退', exact: true }).click()
    await picker.getByRole('button', { name: 'Group A', exact: true }).waitFor()
    await picker.getByRole('button', { name: '前进', exact: true }).click()
    await picker.getByRole('button', { name: 'inside-folder.mp4', exact: true }).waitFor()
    await picker.focus()
    await page.keyboard.press('Alt+ArrowLeft')
    await picker.getByRole('button', { name: 'Group A', exact: true }).waitFor()
    await picker.focus()
    await page.keyboard.press('Alt+ArrowRight')
    await picker.getByRole('button', { name: 'inside-folder.mp4', exact: true }).waitFor()
    await picker.getByRole('button', { name: '打开收藏 C:\\assets', exact: true }).click()
    await picker.getByText('video-preview.mp4', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '取消收藏 C:\\assets', exact: true }).click()
    assert.equal(await picker.getByRole('button', { name: '打开收藏 C:\\assets', exact: true }).count(), 0)
    await picker.getByRole('button', { name: 'Group A', exact: true }).dblclick()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '选择当前文件夹', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).inputValue(), 'C:\\assets\\Group A')
    await hookBrowse.click()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    assert.equal(await picker.getByRole('textbox', { name: '文件夹地址', exact: true }).inputValue(), 'C:\\assets\\Group A')
    await picker.getByRole('textbox', { name: '文件夹地址', exact: true }).fill('C:\\missing')
    assert.equal(await picker.getByRole('button', { name: '选择当前文件夹', exact: true }).isDisabled(), true)
    await picker.getByRole('button', { name: '前往', exact: true }).click()
    await picker.getByRole('alert').waitFor()
    assert.equal(await picker.getByRole('button', { name: '选择当前文件夹', exact: true }).isDisabled(), true)
    await picker.getByRole('textbox', { name: '文件夹地址', exact: true }).fill('C:\\assets\\Empty')
    await picker.getByRole('button', { name: '前往', exact: true }).click()
    await picker.getByText('空文件夹', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '新建文件夹', exact: true }).click()
    const folderNameInput = picker.getByRole('textbox', { name: '文件夹名称', exact: true })
    assert.equal(await folderNameInput.evaluate(el => el === document.activeElement), true)
    await folderNameInput.fill('')
    assert.equal(await picker.getByRole('button', { name: '创建', exact: true }).isDisabled(), true)
    for (const invalidName of ['../outside', '存在', '无权限']) {
      await folderNameInput.fill(invalidName)
      await picker.getByRole('button', { name: '创建', exact: true }).click()
      await picker.getByRole('alert').waitFor()
      assert.equal(await picker.getByText('空文件夹', { exact: true }).count(), 1)
    }
    await page.keyboard.press('Escape')
    assert.equal(await folderNameInput.count(), 0, 'Escape cancels naming without dismissing the picker')
    assert.equal(await picker.isVisible(), true)
    await picker.getByRole('button', { name: '新建文件夹', exact: true }).click()
    await folderNameInput.fill('测试产出')
    await picker.getByRole('button', { name: '创建', exact: true }).click()
    await picker.getByRole('button', { name: '测试产出', exact: true }).waitFor()
    assert.equal(await picker.getByRole('button', { name: '测试产出', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(await picker.getByRole('button', { name: '选择此文件夹', exact: true }).isEnabled(), true)
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-new-folder.png') })
    await page.keyboard.press('Escape')
    assert.equal(await hookBrowse.evaluate(el => el === document.activeElement), true)
    assert.equal(await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).inputValue(), 'C:\\assets\\Group A')
    await page.getByRole('button', { name: '追加', exact: true }).click()
    await picker.getByRole('button', { name: 'Group A', exact: true }).click()
    assert.equal(await picker.getByRole('checkbox', { name: '选择文件夹 Group A', exact: true }).isChecked(), false, 'Management selection must not select a material folder')
    await picker.getByRole('checkbox', { name: '选择文件夹 Group A', exact: true }).click()
    await picker.getByRole('button', { name: '查看文件夹 Group B', exact: true }).click()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '加入当前目录', exact: true }).click()
    await picker.getByRole('button', { name: '确认 2 个文件夹', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: '文件夹', exact: true }).inputValue(), 'C:\\assets\\Group A; C:\\assets\\Group B')
    await page.getByRole('button', { name: '追加', exact: true }).click()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    assert.equal(await picker.getByRole('textbox', { name: '文件夹地址', exact: true }).inputValue(), 'C:\\assets\\Group B')
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-picker.png') })
    await picker.getByRole('button', { name: '新建文件夹', exact: true }).click()
    await folderNameInput.fill('新的Body')
    await picker.getByRole('button', { name: '创建', exact: true }).click()
    await picker.getByRole('checkbox', { name: '选择文件夹 新的Body', exact: true }).waitFor()
    assert.equal(await picker.getByRole('checkbox', { name: '选择文件夹 新的Body', exact: true }).isChecked(), true)
    assert.equal(await picker.getByRole('button', { name: '确认 1 个文件夹', exact: true }).isEnabled(), true)
    await picker.getByRole('button', { name: 'inside-folder.mp4', exact: true }).dblclick()
    assert((await page.evaluate(() => window.directoryOperationCalls)).some(call => call[0] === 'open'))
    await picker.getByRole('button', { name: 'inside-folder.mp4', exact: true }).click()
    await page.keyboard.press('F2')
    const renameNameInput = picker.getByRole('textbox', { name: '新名称', exact: true })
    assert.equal(await renameNameInput.inputValue(), 'inside-folder')
    await renameNameInput.fill('存在')
    await picker.getByRole('button', { name: '保存名称', exact: true }).click()
    await picker.getByText('同名文件或文件夹已存在，不会覆盖。', { exact: true }).waitFor()
    await renameNameInput.fill('renamed-video')
    await picker.getByRole('button', { name: '保存名称', exact: true }).click()
    await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).waitFor()
    await page.evaluate(() => { window.menuAction = 'copy' })
    await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).click({ button: 'right' })
    await page.waitForFunction(() => window.directoryOperationCalls.some(call => call[0] === 'copy'))
    await page.evaluate(() => { window.menuAction = 'reveal' })
    await picker.getByRole('button', { name: '操作', exact: true }).click()
    await page.waitForFunction(() => window.directoryOperationCalls.some(call => call[0] === 'reveal'))
    await picker.getByRole('button', { name: '清除管理选中', exact: true }).click()
    await picker.getByRole('button', { name: '新的Body', exact: true }).click()
    await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).click({ modifiers: ['Shift'] })
    await picker.getByText('管理选中 3 项', { exact: true }).waitFor()
    assert.equal(await picker.getByRole('checkbox', { name: '选择文件夹 新的Body', exact: true }).isChecked(), true)
    await page.keyboard.press('Control+a')
    await picker.getByText('管理选中 3 项', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '清除管理选中', exact: true }).click()
    await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).click()
    await picker.getByRole('button', { name: 'music.wav', exact: true }).click({ modifiers: ['Control'] })
    await picker.getByText('管理选中 2 项', { exact: true }).waitFor()
    await page.keyboard.press('Control+c')
    await page.waitForFunction(() => window.directoryOperationCalls.filter(call => call[0] === 'copy').some(call => call[1].length === 2))
    await page.keyboard.press('Delete')
    await page.waitForFunction(() => window.directoryOperationCalls.some(call => call[0] === 'trash'))
    assert.equal(await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).count(), 1, 'Cancel must not remove files')
    await page.evaluate(() => { window.trashMode = 'partial' })
    await picker.getByRole('button', { name: '移入回收站', exact: true }).click()
    await picker.getByText(/1 项未删除/).waitFor()
    assert.equal(await picker.getByRole('button', { name: 'renamed-video.mp4', exact: true }).count(), 0)
    assert.equal(await picker.getByRole('button', { name: 'music.wav', exact: true }).count(), 1, 'Failed items stay visible')
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-management.png') })
    await picker.getByRole('button', { name: '取消', exact: true }).click()
    await page.getByRole('button', { name: '简易教程', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: '码率', exact: true }).inputValue(), '8000k')
    assert.equal(await page.getByRole('dialog', { name: '简易教程' }).isVisible(), true)
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('dialog', { name: '简易教程' }).isVisible(), false)
    assert.equal(await page.getByRole('button', { name: '简易教程', exact: true }).evaluate(el => el === document.activeElement), true)
    await page.getByRole('button', { name: 'Light', exact: true }).click()
    await hookBrowse.click()
    await picker.getByText('inside-folder.mp4', { exact: true }).waitFor()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-details-light.png') })
    await picker.getByRole('button', { name: '缩略图', exact: true }).click()
    if (nativeResults) await picker.getByRole('img', { name: 'inside-folder.mp4 缩略图', exact: true }).waitFor()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-directory-thumbnails-light.png') })
    await picker.getByRole('button', { name: '取消', exact: true }).click()
    await page.getByRole('checkbox', { name: '普通 Body 按原素材时长', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: '后段', exact: true }).isDisabled(), true)
    assert.equal(await page.getByRole('textbox', { name: 'Body', exact: true }).isDisabled(), true)
    await page.getByRole('checkbox', { name: '普通 Body 按原素材时长', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: '后段', exact: true }).isEnabled(), true)
    await page.getByRole('button', { name: '分组', exact: true }).click()
    await page.getByRole('checkbox', { name: 'Body 1 按原素材时长', exact: true }).click()
    assert.equal(await page.getByRole('spinbutton', { name: 'Body 1 每段时长', exact: true }).isDisabled(), true)
    assert.equal(await page.getByRole('spinbutton', { name: 'Body 2 每段时长', exact: true }).isEnabled(), true)
    await page.getByRole('checkbox', { name: 'Body 1 按原素材时长', exact: true }).click()
    const colors = await page.locator('input:not([type=checkbox])').evaluateAll(inputs => inputs.map(el => {
      const css = getComputedStyle(el); return { bg: css.backgroundColor, text: css.color }
    }))
    assert(colors.length > 10)
    assert(colors.every(c => c.bg === 'rgb(255, 255, 255)' && c.text !== 'rgb(255, 255, 255)'), JSON.stringify(colors))
    await page.getByRole('button', { name: '全片 BGM说明' }).click()
    const help = page.getByRole('dialog', { name: '全片 BGM说明' })
    assert.equal(await help.isVisible(), true)
    const bounds = await help.boundingBox()
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 1440 && bounds.y + bounds.height <= 1080)
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-light-help.png') })
    await page.mouse.click(1435, 1075)
    assert.equal(await help.isVisible(), false)
    assert.equal(await page.getByRole('button', { name: '按全片 BGM 时长', exact: true }).isEnabled(), true)
    await page.getByRole('button', { name: '按全片 BGM 时长', exact: true }).click()
    await page.getByText('请先选择并启用全片 BGM，再开启按全片 BGM 时长', { exact: true }).waitFor()
    assert.equal(await page.getByRole('textbox', { name: '全片 BGM 路径', exact: true }).evaluate(el => el === document.activeElement), true)
    assert.equal(await page.getByText('可选', { exact: true }).count(), 4)
    assert(Math.abs(await page.locator('.feature-help-glyph').first().evaluate(el => parseFloat(getComputedStyle(el).width)) - 9) < .1)
    await page.getByRole('textbox', { name: '全片 BGM 路径', exact: true }).fill('music')
    await page.getByRole('button', { name: '按全片 BGM 时长', exact: true }).click()
    await page.getByText('预计成片 25.000–25.000s', { exact: true }).waitFor()
    await page.getByRole('button', { name: '＋Hook 配乐', exact: true }).click()
    await page.getByRole('button', { name: '＋Body 配乐', exact: true }).click()
    await page.getByRole('textbox', { name: 'Hook BGM 路径', exact: true }).fill('hook.wav')
    await page.getByRole('textbox', { name: 'Body BGM 路径', exact: true }).fill('body.wav')
    await page.getByRole('button', { name: '成品 Hook', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '成品 Hook', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(await page.getByRole('textbox', { name: '全片 BGM 路径', exact: true }).inputValue(), 'music')
    assert.equal(await page.getByRole('textbox', { name: 'Hook BGM 路径', exact: true }).inputValue(), 'hook.wav')
    await page.getByRole('textbox', { name: 'Hook BGM 路径', exact: true }).fill('newhook.wav')
    assert.equal(await page.getByRole('button', { name: '成品 Hook', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByText('全片 BGM 设置', { exact: true }).click()
    await page.getByText('Hook BGM 设置', { exact: true }).click()
    await page.getByText('Body BGM 设置', { exact: true }).click()
    assert.equal(await page.getByRole('combobox', { name: '全片 BGM 播放起点' }).isDisabled(), true)
    assert.equal(await page.getByRole('combobox', { name: 'Hook BGM 播放起点' }).isEnabled(), true)
    await page.getByRole('spinbutton', { name: 'Hook BGM 渐入', exact: true }).fill('0.5')
    await page.getByRole('spinbutton', { name: 'Body BGM 渐出', exact: true }).fill('0.8')
    await page.getByRole('spinbutton', { name: '全片 BGM 音量', exact: true }).fill('0')
    assert.equal(await page.getByRole('button', { name: '按全片 BGM 时长', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByRole('checkbox', { name: '启用全片 BGM', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '按片段数量', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(await page.getByRole('textbox', { name: 'Body BGM 路径', exact: true }).inputValue(), 'body.wav')
    await page.getByRole('checkbox', { name: '启用全片 BGM', exact: true }).click()
    await page.getByRole('button', { name: '按全片 BGM 时长', exact: true }).click()
    await page.getByRole('textbox', { name: '全片 BGM 路径', exact: true }).fill('')
    assert.equal(await page.getByRole('button', { name: '按片段数量', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByText('全片 BGM 已清空或关闭，已恢复按片段数量生成', { exact: true }).first().waitFor()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-three-track-light.png') })
    await page.getByRole('button', { name: '简易教程', exact: true }).click()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-light-tutorial.png') })
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Dark', exact: true }).click()
    await page.getByText('建议打开', { exact: true }).waitFor()
    taskLogs.push(...Array.from({ length: 150 }, (_, i) => `回归日志 ${i}`))
    await page.getByText('回归日志 149', { exact: true }).waitFor()
    await hookBrowse.click()
    await picker.getByRole('button', { name: 'inside-folder.mp4', exact: true }).click()
    assert.equal(await picker.getByRole('button', { name: '重命名', exact: true }).isDisabled(), true)
    assert.equal(await picker.getByRole('button', { name: '移入回收站', exact: true }).isDisabled(), true)
    const callsBeforeBlockedDelete = await page.evaluate(() => window.directoryOperationCalls.filter(call => call[0] === 'trash').length)
    await page.keyboard.press('Delete')
    await picker.getByText('混剪任务进行中，暂禁删除和改名。', { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.directoryOperationCalls.filter(call => call[0] === 'trash').length), callsBeforeBlockedDelete)
    await picker.getByRole('button', { name: '取消', exact: true }).click()
    const logPanel = page.locator('[aria-label="运行日志"]')
    const atBottom = () => logPanel.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop < 10)
    assert.equal(await atBottom(), true)
    await logPanel.evaluate(el => { el.scrollTop = 80; el.dispatchEvent(new Event('scroll', { bubbles: true })) })
    taskLogs.push('新增日志不抢滚动位置')
    await page.getByText('新增日志不抢滚动位置', { exact: true }).waitFor({ state: 'attached' })
    assert(Math.abs(await logPanel.evaluate(el => el.scrollTop) - 80) < 2)
    await page.getByRole('button', { name: /^产出/ }).click()
    await page.getByRole('button', { name: /^日志/ }).click()
    assert(Math.abs(await logPanel.evaluate(el => el.scrollTop) - 80) < 2)
    await logPanel.evaluate(el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll', { bubbles: true })) })
    taskLogs.push('恢复底部跟随')
    await page.getByText('恢复底部跟随', { exact: true }).waitFor()
    assert.equal(await atBottom(), true)
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-dark.png') })
    taskLogs.splice(0)
    await page.getByRole('button', { name: '普通', exact: true }).click()
    await hookBrowse.click()
    await picker.getByRole('button', { name: '收藏当前目录', exact: true }).click()
    await picker.getByRole('button', { name: '上一级', exact: true }).click()
    await picker.getByRole('button', { name: 'Group A', exact: true }).click()
    await page.waitForFunction(() => !Array.from(document.querySelectorAll('dialog[open] button')).find(el => el.textContent === '重命名')?.disabled)
    await page.keyboard.press('F2')
    await renameNameInput.fill('Renamed Hook')
    await picker.getByRole('button', { name: '保存名称', exact: true }).click()
    await picker.getByRole('button', { name: 'Renamed Hook', exact: true }).waitFor()
    assert.equal(await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).inputValue(), 'C:\\assets\\Renamed Hook')
    assert.match(await page.getByRole('textbox', { name: '文件夹', exact: true }).inputValue(), /Renamed Hook/)
    assert.equal(await picker.getByRole('button', { name: '打开收藏 C:\\assets\\Renamed Hook', exact: true }).count(), 1)
    await page.evaluate(() => { window.trashMode = 'success' })
    await picker.getByRole('button', { name: '移入回收站', exact: true }).click()
    await picker.getByRole('button', { name: 'Renamed Hook', exact: true }).waitFor({ state: 'detached' })
    assert.equal(await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).inputValue(), '')
    assert.equal(await picker.getByRole('button', { name: '打开收藏 C:\\assets\\Renamed Hook', exact: true }).count(), 0)
    assert.equal(await page.getByRole('textbox', { name: '文件夹', exact: true }).inputValue(), 'C:\\assets\\Group B')
    await picker.getByRole('button', { name: '取消', exact: true }).click()
    // Post-processing is still running: no premature final output or celebration.
    taskFixtures = [{ task_id: 'output-status-test', task_name: '封面状态回归', status: 'running',
      log_lines: [], output_files: [], progress: 50, current: 0, total: 2,
      message: '基础混剪完成，随机封面处理中' }]
    await page.getByRole('button', { name: /^任务/ }).click()
    await page.getByText('基础混剪完成，随机封面处理中', { exact: true }).waitFor()
    await page.getByRole('button', { name: /^产出/ }).click()
    await page.getByText('暂无产出', { exact: true }).waitFor()
    assert.equal(await page.getByText('任务完成', { exact: true }).count(), 0)
    const warningFile = 'C:\\output\\保留基础视频.mp4'
    const finalFile = '/output/正常成品.mp4'
    const outputWarning = '随机封面未完成：用户已停止，保留基础视频。'
    taskFixtures = [{ ...taskFixtures[0], status: 'partial', progress: 100, current: 2,
      message: '已保留 2 条视频，其中 1 条后处理未完成', output_files: [warningFile, finalFile],
      output_warnings: { [warningFile]: outputWarning }, output_elapsed: { [warningFile]: 12.5, [finalFile]: 13 } }]
    await page.getByRole('status').filter({ hasText: '部分完成：封面状态回归' }).waitFor()
    await page.getByText('保留基础视频.mp4', { exact: true }).waitFor()
    await page.getByText('正常成品.mp4', { exact: true }).waitFor()
    assert.equal(await page.getByText('任务完成', { exact: true }).count(), 0)
    assert.equal(await page.getByText('有警告 · 后处理未完成', { exact: true }).count(), 1)
    await page.getByText('有警告 · 后处理未完成', { exact: true }).click()
    await page.getByText(outputWarning, { exact: true }).waitFor()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-output-warning-dark.png') })
    await page.getByRole('button', { name: /^任务/ }).click()
    await page.getByText('部分完成', { exact: true }).waitFor()
    await page.getByText(taskFixtures[0].message, { exact: true }).waitFor()
    await page.getByRole('button', { name: 'Light', exact: true }).click()
    await page.getByRole('button', { name: /^产出/ }).click()
    await page.getByText('有警告 · 后处理未完成', { exact: true }).click()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-output-warning-light.png') })
    // A row stop request must affect just that task, never the other active rows.
    const taskDefaults = { log_lines: [], output_files: [], progress: 0, current: 0, total: 2, message: '' }
    taskFixtures = [
      { ...taskDefaults, task_id: 'stop-a', task_name: '任务甲', status: 'running' },
      { ...taskDefaults, task_id: 'stop-b', task_name: '任务乙', status: 'pending' },
      { ...taskDefaults, task_id: 'already-done', task_name: '已完成任务', status: 'completed', progress: 100 },
    ]
    await page.getByRole('button', { name: /^任务/ }).click()
    const stopA = page.getByRole('button', { name: '停止任务 任务甲', exact: true })
    const stopB = page.getByRole('button', { name: '停止任务 任务乙', exact: true })
    await stopA.waitFor()
    assert.equal(await stopB.isEnabled(), true)
    assert.equal(await page.getByRole('button', { name: '停止任务 已完成任务', exact: true }).count(), 0)
    await stopA.click()
    await page.getByText('停止中…', { exact: true }).waitFor()
    assert.equal(await stopA.isDisabled(), true)
    // Force a duplicate DOM click: disabled state and the request guard reject it.
    await stopA.evaluate(button => button.click())
    assert.equal(await stopB.isEnabled(), true)
    assert.deepEqual(taskStopCalls, ['stop-a'])
    assert(pendingStopRoute)
    taskFixtures[0] = { ...taskFixtures[0], status: 'stopped', message: '任务甲已停止' }
    await pendingStopRoute.fulfill({ json: { message: '停止指令已发送' } })
    pendingStopRoute = null
    await page.getByText('任务甲已停止', { exact: true }).waitFor()
    assert.equal(await stopA.count(), 0)
    assert.deepEqual(taskGetCalls, ['stop-a'])
    assert.deepEqual(taskStopCalls, ['stop-a'])
    assert.equal(await stopB.isEnabled(), true)
    failNextStop = true
    await stopB.click()
    await page.getByRole('status').filter({ hasText: '停止失败：任务乙。测试停止失败' }).waitFor()
    assert.equal(await stopB.isEnabled(), true)
    assert.deepEqual(taskStopCalls, ['stop-a', 'stop-b'])
    assert.equal(taskFixtures[1].status, 'pending')
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-task-stop-light.png') })
    // Production and benchmarking must not compete; preserve the existing stop isolation assertions above.
    const benchmarkButton = page.getByRole('button', { name: '智能压测', exact: true })
    const concurrencyInput = page.getByRole('textbox', { name: '并发', exact: true })
    await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).fill('C:\\assets\\benchmark-hook')
    await concurrencyInput.fill('4')
    assert.equal(await benchmarkButton.isDisabled(), true)
    assert.equal(benchmarkCalls.length, 0)
    taskFixtures[1] = { ...taskFixtures[1], status: 'running', message: '生产任务仍在运行' }
    await page.getByText('生产任务仍在运行', { exact: true }).waitFor()
    assert.equal(await benchmarkButton.isDisabled(), true)
    assert.equal(benchmarkCalls.length, 0)
    taskFixtures = []
    await page.getByText('暂无任务', { exact: true }).waitFor()
    await benchmarkButton.click()
    const realProgress = page.getByRole('button', { name: '压测中 17%', exact: true })
    await realProgress.waitFor()
    assert.equal(await page.getByRole('button', { name: '启动渲染', exact: true }).isDisabled(), true)
    // Keep backend progress unchanged beyond several old fake-progress timer ticks.
    await page.waitForTimeout(2600)
    assert.equal(await realProgress.count(), 1)
    taskFixtures[0] = { ...taskFixtures[0], progress: 43, current: 3, message: '测试服务端真实进度 43%' }
    await page.getByRole('button', { name: '压测中 43%', exact: true }).waitFor()
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-benchmark-progress.png') })
    // Cancel a registered benchmark through its individual task button.
    await page.getByRole('button', { name: '停止任务 智能压测回归 1', exact: true }).click()
    await page.getByText('停止中…', { exact: true }).waitFor()
    assert.equal(taskStopCalls.at(-1), 'benchmark-ui-1')
    taskFixtures[0] = { ...taskFixtures[0], status: 'stopped', message: '测试压测已停止' }
    await pendingStopRoute.fulfill({ json: { message: '停止指令已发送' } })
    pendingStopRoute = null
    await page.getByText('测试压测已停止', { exact: true }).waitFor()
    await pendingBenchmarkRoute.fulfill({ json: { error: '用户已停止压测', cancelled: true, best_concurrent: null, results: {} } })
    pendingBenchmarkRoute = null
    await benchmarkButton.waitFor()
    assert.equal(await concurrencyInput.inputValue(), '4')
    assert.equal(await page.getByText('任务完成', { exact: true }).count(), 0)
    assert.equal(await page.getByRole('status').filter({ hasText: '任务完成：智能压测' }).count(), 0)
    // Unstable/no-recommendation results keep the user's configured concurrency.
    await benchmarkButton.click()
    await realProgress.waitFor()
    taskFixtures[0] = { ...taskFixtures[0], status: 'completed', progress: 100, message: '测试无推荐已结束' }
    await pendingBenchmarkRoute.fulfill({ json: { best_concurrent: null, note: '不稳定样本不推荐', sample_count: 4,
      results: { 4: { concurrent: 4, total_time: 35, avg_video_elapsed: 28, stable: false, reason: '测试 GPU 重试' } },
      verification_results: {} } })
    pendingBenchmarkRoute = null
    await page.getByRole('status').filter({ hasText: '压测未得到可靠推荐，当前并发设置未改变' }).waitFor()
    await benchmarkButton.waitFor()
    await page.getByText('测试无推荐已结束', { exact: true }).waitFor()
    assert.equal(await concurrencyInput.inputValue(), '4')
    assert.equal(await page.getByText('任务完成', { exact: true }).count(), 0)
    assert.equal(await page.getByRole('status').filter({ hasText: '任务完成：智能压测' }).count(), 0)
    // A reliable recommendation applies once; report throughput and actual latency separately.
    await benchmarkButton.click()
    await realProgress.waitFor()
    taskFixtures[0] = { ...taskFixtures[0], status: 'completed', progress: 100, message: '测试可靠推荐已结束' }
    await pendingBenchmarkRoute.fulfill({ json: { best_concurrent: 2, sample_count: 4,
      results: { 2: { concurrent: 2, total_time: 26, videos_per_minute: 9.23, avg_video_elapsed: 11, stable: true } },
      verification_results: { 2: { concurrent: 2, total_time: 27, videos_per_minute: 8.89, avg_video_elapsed: 12, stable: true } } } })
    pendingBenchmarkRoute = null
    await page.getByRole('status').filter({ hasText: '压测建议 2 路并发' }).waitFor()
    await benchmarkButton.waitFor()
    await page.getByText('测试可靠推荐已结束', { exact: true }).waitFor()
    assert.equal(await concurrencyInput.inputValue(), '2')
    assert.equal(benchmarkCalls.length, 3)
    assert.equal(await page.getByText('任务完成', { exact: true }).count(), 0)
    await page.getByRole('button', { name: /^日志/ }).click()
    await page.getByText(/\[初测\] 2 路，同批 4 条总耗时 26 秒，9\.23 条\/分，单条实际平均 11 秒/).waitFor()
    await page.getByText(/\[复测\] 2 路，同批 4 条总耗时 27 秒，8\.89 条\/分，单条实际平均 12 秒/).waitFor()
    await page.getByText(/不稳定：测试 GPU 重试/).waitFor()
    taskFixtures = null
    // Migrating legacy Body-duration settings must notify once and preserve music.
    await page.evaluate(() => localStorage.setItem('vm-config', JSON.stringify({ bgm_dir: 'legacy.wav',
      vol_bgm: 45, apply_bgm_to_hook: false, duration_mode: 'bgm' })))
    await page.reload()
    await page.getByText(/^旧版 Body 音乐与音量已保留/).waitFor()
    assert.equal(await page.getByRole('textbox', { name: 'Body BGM 路径', exact: true }).inputValue(), 'legacy.wav')
    assert.equal(await page.getByRole('spinbutton', { name: 'Body BGM 音量', exact: true }).inputValue(), '45')
    assert.equal(await page.getByRole('button', { name: '按片段数量', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(await page.evaluate(() => localStorage.getItem('vm-bgm-migration-notice')), null)
    await page.reload()
    assert.equal(await page.getByText(/^旧版 Body 音乐与音量已保留/).count(), 0)
    assert.deepEqual(errors, [])
    console.log('UI smoke passed: directory/BGM controls, final-only outputs, partial warnings, isolated stopping, production/benchmark exclusion, real benchmark progress, cancellation, stable recommendations and throughput/latency reporting.')
  } finally {
    await browser?.close()
    await new Promise(resolve => server.close(resolve))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
