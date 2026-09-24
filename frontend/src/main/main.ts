import { app, BrowserWindow, ipcMain, dialog, shell, Menu } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import path from 'path'
import os from 'os'
import fs from 'fs'
import { randomUUID } from 'crypto'
import { validateIdentity } from './backendHandshake'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let isQuitting = false

const PREPARE_ARG = '--videomatrix-prepare='
const PREPARE_FIELDS = new Set([
  'hook_full_duration',
  'task_name', 'hook_dir', 'body_dirs', 'body_mode', 'body_groups', 'bgm_dir', 'voice_dir', 'srt_dir',
  'watermark_path', 'base_out_dir', 't_hook', 't_body', 'total_clips',
  'target_count', 'hook_r', 'body_r', 'bgm_r', 'resolution', 'fps', 'bitrate',
  'vol_orig', 'vol_hook_orig', 'vol_bgm', 'vol_voice', 'apply_bgm_to_hook',
  'apply_voice_to_hook', 'apply_srt_to_hook', 'apply_watermark_to_hook',
  'enable_srt', 'subtitle_y_percent', 'subtitle_font_size_percent', 'enable_gpu',
  'concurrent_tasks', 'enable_variants',
  'variant_strength', 'variant_hook', 'variant_body', 'variant_mirror',
  'variant_frame_mix', 'variant_seed',
  'enable_random_cover',
  'random_cover_mode',
])

interface PrepareRequest {
  config: Record<string, unknown>
  ackPath: string
}

function readPrepareUpdate(argv: string[]): PrepareRequest | null {
  const argument = argv.find((value) => value.startsWith(PREPARE_ARG))
  if (!argument) return null
  const payloadPath = path.resolve(argument.slice(PREPARE_ARG.length))
  const tempDir = path.resolve(os.tmpdir()).toLowerCase()
  const validPayloadPath = path.dirname(payloadPath).toLowerCase() === tempDir
    && path.basename(payloadPath).startsWith('videomatrix-prepare-')
  if (!validPayloadPath) return null
  try {
    const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8')) as {
      version?: number
      config?: Record<string, unknown>
      ack_path?: string
    }
    if (payload.version !== 1 || !payload.config || typeof payload.config !== 'object') return null
    const ackPath = typeof payload.ack_path === 'string' ? path.resolve(payload.ack_path) : ''
    const validAckPath = ackPath
      && path.dirname(ackPath).toLowerCase() === tempDir
      && path.basename(ackPath).startsWith('videomatrix-prepare-ack-')
    return {
      config: Object.fromEntries(
        Object.entries(payload.config).filter(([key]) => PREPARE_FIELDS.has(key))
      ),
      ackPath: validAckPath ? ackPath : '',
    }
  } catch (error) {
    errorDev(`[Agent] failed to read prepare payload: ${String(error)}`)
    return null
  } finally {
    try { fs.unlinkSync(payloadPath) } catch { /* one-shot file may already be gone */ }
  }
}

const isDev = process.env.NODE_ENV === 'development'
let backendPort = 0
const backendInstance = randomUUID()
let backendVerified = false
const runtimeFile = path.join(process.env.APPDATA || os.homedir(), 'VideoMatrix', 'desktop-runtime.json')
const hasSingleInstanceLock = app.requestSingleInstanceLock()
let pendingPrepareRequest = hasSingleInstanceLock ? readPrepareUpdate(process.argv) : null

function logDev(message: string) {
  if (isDev) {
    console.log(message)
  }
}

function errorDev(message: string) {
  if (isDev) {
    console.error(message)
  }
}

function getDevBackendDir(): string {
  return path.join(__dirname, '../../../backend')
}

/**
 * Locate the backend binary produced by PyInstaller.
 * In production it sits inside extraResources/backend/.
 * Returns null in dev mode (we use the Python interpreter instead).
 */
function getBundledBackendBinary(): string | null {
  if (isDev) return null
  const exeName = os.platform() === 'win32' ? 'videomatrix-backend.exe' : 'videomatrix-backend'
  const candidate = path.join(process.resourcesPath, 'backend', exeName)
  return fs.existsSync(candidate) ? candidate : null
}

async function startBackend(): Promise<void> {
  const bundled = getBundledBackendBinary()
  if (!bundled && !isDev) {
    throw new Error('安装目录缺少后端程序，请重新安装完整安装包。')
  }
  const child = spawn(bundled || (process.env.VIDEOMATRIX_PYTHON || (os.platform() === 'win32' ? 'python' : 'python3')),
    [...(bundled ? [] : ['run_backend.py']), '--port', '0', '--host', '127.0.0.1'], {
      cwd: bundled ? path.dirname(bundled) : getDevBackendDir(),
      stdio: 'pipe', windowsHide: true, detached: process.platform !== 'win32',
      env: { ...process.env, VIDEOMATRIX_INSTANCE: backendInstance, PYTHONIOENCODING: 'utf-8' },
    })
  backendProcess = child
  let errors = ''
  child.stderr?.on('data', data => { errors = (errors + data.toString()).slice(-4000) })
  child.stdin?.on('error', () => { /* Child may have exited before shutdown. */ })
  await new Promise<void>((resolve, reject) => {
    let settled = false
    let buffer = ''
    let checking = false
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (error) reject(error)
      else resolve()
    }
    const timer = setTimeout(() => finish(new Error(`后端启动超时，可能安装不完整或被安全软件阻止。\n${errors}`)), 45000)
    child.on('error', error => finish(error))
    child.on('close', code => {
      if (!settled) finish(new Error(`后端启动失败（${code}）。\n${errors}`))
      else if (backendVerified && !isQuitting) {
        backendVerified = false
        dialog.showErrorBox('VideoMatrix 后端已退出', `请重新打开软件。\n${errors}`)
        void shutdownApp()
      }
    })
    child.stdout?.on('data', data => {
      buffer += data.toString()
      let end: number
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).trim()
        buffer = buffer.slice(end + 1)
        if (!line.startsWith('VIDEOMATRIX_READY ') || checking || settled) continue
        checking = true
        void (async () => {
          try {
            const ready = JSON.parse(line.slice('VIDEOMATRIX_READY '.length))
            validateIdentity(ready, app.getVersion(), backendInstance)
            backendPort = ready.port
            while (!settled && !isQuitting) {
              let health: any
              try {
                const response = await fetch(`http://127.0.0.1:${backendPort}/api/health`, { signal: AbortSignal.timeout(1500) })
                if (response.ok) health = await response.json()
              } catch { /* Socket was bound before uvicorn finished startup. */ }
              if (health) {
                validateIdentity({ ...health, port: backendPort }, app.getVersion(), backendInstance)
                if (health.status !== 'ok') throw new Error('后端健康检查失败。')
                backendVerified = true
                try {
                  fs.mkdirSync(path.dirname(runtimeFile), { recursive: true })
                  const temporary = `${runtimeFile}.${backendInstance}.tmp`
                  fs.writeFileSync(temporary, JSON.stringify({ ...health, port: backendPort }), 'utf8')
                  fs.renameSync(temporary, runtimeFile)
                } catch (error) { errorDev(`无法保存本地连接信息：${String(error)}`) }
                finish()
                return
              }
              await new Promise(done => setTimeout(done, 150))
            }
          } catch (error) { finish(error instanceof Error ? error : new Error(String(error))) }
        })()
      }
      if (buffer.length > 16000) buffer = buffer.slice(-16000)
    })
  })
}

async function stopBackend() {
  const child = backendProcess
  backendProcess = null
  backendVerified = false
  try {
    if (JSON.parse(fs.readFileSync(runtimeFile, 'utf8')).instance === backendInstance) fs.unlinkSync(runtimeFile)
  } catch { /* Another instance may own the discovery file. */ }
  if (!child?.pid || child.exitCode !== null) return
  // Kill only the process tree launched by this instance, including the
  // PyInstaller worker and FFmpeg children; never kill by executable name.
  if (process.platform === 'win32') {
    await new Promise<void>(resolve => {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true })
      killer.on('close', () => resolve())
      killer.on('error', () => { child.stdin?.end(); child.kill(); resolve() })
    })
  } else {
    try { process.kill(-child.pid, 'SIGTERM') } catch { child.stdin?.end() }
    await new Promise(done => setTimeout(done, 500))
    try { process.kill(-child.pid, 'SIGKILL') } catch { /* Already exited. */ }
  }
}

async function stopBackendTasks(timeoutMs = 2500) {
  if (!backendVerified) return
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const health = await fetch(`http://127.0.0.1:${backendPort}/api/health`, { signal: controller.signal }).then(r => r.json())
    validateIdentity({ ...health, port: backendPort }, app.getVersion(), backendInstance)
    await fetch(`http://127.0.0.1:${backendPort}/api/tasks/stop-all`, {
      method: 'POST',
      signal: controller.signal,
    })
  } catch {
    // Backend may already be down; closing should still proceed.
  } finally {
    clearTimeout(timer)
  }
}

async function shutdownApp() {
  if (isQuitting) return
  isQuitting = true
  await stopBackendTasks()
  await stopBackend()
  mainWindow?.destroy()
  app.quit()
}

function createWindow() {
  Menu.setApplicationMenu(null)

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 1080,
    minWidth: 1280,
    minHeight: 1080,
    maxWidth: 1280,
    maxHeight: 1080,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    autoHideMenuBar: true,
    title: `VideoMatrix ${app.getVersion()}`,
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
    titleBarStyle: 'hiddenInset',
  })

  mainWindow.on('page-title-updated', event => event.preventDefault())

  // 加载渲染进程
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.on('did-finish-load', () => {
    if (!mainWindow || !pendingPrepareRequest) return
    const request = pendingPrepareRequest
    pendingPrepareRequest = null
    const serialized = JSON.stringify(request.config).replace(/</g, '\\u003c')
    void mainWindow.webContents.executeJavaScript(`
      (() => {
        let current = {};
        try { current = JSON.parse(localStorage.getItem('vm-config') || '{}'); } catch {}
        localStorage.setItem('vm-config', JSON.stringify({ ...current, ...${serialized} }));
      })()
    `).then(() => {
      if (request.ackPath) fs.writeFileSync(request.ackPath, '{"ok":true}', 'utf8')
      mainWindow?.webContents.reload()
    })
  })

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault()
      void shutdownApp()
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function resolveDialogDefaultPath(rawPath?: string): string | undefined {
  if (!rawPath?.trim()) return undefined

  let candidate = path.resolve(rawPath.trim())
  try {
    if (fs.statSync(candidate).isFile()) candidate = path.dirname(candidate)
  } catch {
    let parent = candidate
    while (true) {
      const next = path.dirname(parent)
      if (next === parent) return undefined
      parent = next
      try {
        if (fs.statSync(parent).isDirectory()) {
          candidate = parent
          break
        }
      } catch { /* keep walking to the nearest existing parent */ }
    }
  }

  try {
    return fs.statSync(candidate).isDirectory() ? candidate : undefined
  } catch {
    return undefined
  }
}

// IPC 处理器
ipcMain.handle('dialog:openDirectory', async (_, defaultPath?: string, multi = false) => {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: multi ? ['openDirectory', 'multiSelections'] : ['openDirectory'],
    defaultPath: resolveDialogDefaultPath(defaultPath),
  })
  if (result.canceled) return null
  return multi ? result.filePaths : result.filePaths[0]
})

ipcMain.handle('dialog:openFile', async (_, filters, defaultPath?: string) => {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: filters || [{ name: 'All Files', extensions: ['*'] }],
    defaultPath: resolveDialogDefaultPath(defaultPath),
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('dialog:saveText', async (_, defaultName: string, content: string, filters) => {
  if (!mainWindow) return null
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName,
    filters: filters || [{ name: 'Text', extensions: ['txt'] }],
  })
  if (result.canceled || !result.filePath) return null
  fs.writeFileSync(result.filePath, content, 'utf8')
  return result.filePath
})

ipcMain.handle('shell:openPath', async (_, filePath: string) => {
  await shell.openPath(filePath)
})

ipcMain.handle('app:getBackendPort', () => backendPort)

// 应用生命周期
if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', (_event, commandLine) => {
    const request = readPrepareUpdate(commandLine)
    if (request) pendingPrepareRequest = request
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
      if (request) mainWindow.webContents.reload()
    }
  })

  app.whenReady().then(async () => {
    try {
      await startBackend()
      if (!isQuitting) createWindow()
    } catch (error) {
      dialog.showErrorBox('VideoMatrix 启动失败', String(error))
      await shutdownApp()
    }
  })

  app.on('window-all-closed', () => {
    if (!isQuitting) {
      void shutdownApp()
    }
  })

  app.on('activate', () => {
    if (mainWindow === null && backendVerified && !isQuitting) {
      createWindow()
    }
  })

  app.on('before-quit', event => {
    if (!isQuitting) {
      event.preventDefault()
      void shutdownApp()
    }
  })
}
