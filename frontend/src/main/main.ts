import { app, BrowserWindow, ipcMain, dialog, shell, Menu } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import path from 'path'
import os from 'os'
import fs from 'fs'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let isQuitting = false

const PREPARE_ARG = '--videomatrix-prepare='
const PREPARE_FIELDS = new Set([
  'task_name', 'hook_dir', 'body_dirs', 'bgm_dir', 'voice_dir', 'srt_dir',
  'watermark_path', 'base_out_dir', 't_hook', 't_body', 'total_clips',
  'target_count', 'hook_r', 'body_r', 'bgm_r', 'resolution', 'fps', 'bitrate',
  'vol_orig', 'vol_hook_orig', 'vol_bgm', 'vol_voice', 'apply_bgm_to_hook',
  'apply_voice_to_hook', 'apply_srt_to_hook', 'apply_watermark_to_hook',
  'enable_srt', 'enable_gpu', 'concurrent_tasks', 'enable_variants',
  'variant_strength', 'variant_hook', 'variant_body', 'variant_mirror',
  'variant_frame_mix', 'variant_seed',
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
const BACKEND_PORT = 8765
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

function startBackend() {
  const bundled = getBundledBackendBinary()

  if (bundled) {
    // Production path — single self-contained binary, no Python required.
    logDev(`[Main] Launching bundled backend: ${bundled} --port ${BACKEND_PORT}`)
    backendProcess = spawn(
      bundled,
      ['--port', String(BACKEND_PORT), '--host', '127.0.0.1'],
      {
        cwd: path.dirname(bundled),
        stdio: 'ignore',
        env: { ...process.env },
      }
    )
  } else {
    // Dev path — assume system Python + project venv installed deps.
    const backendDir = getDevBackendDir()
    const python = os.platform() === 'win32' ? 'python' : 'python3'
    logDev(`[Main] Launching dev backend: ${python} -m uvicorn app.main:app --port ${BACKEND_PORT}`)
    backendProcess = spawn(
      python,
      ['-m', 'uvicorn', 'app.main:app', '--port', String(BACKEND_PORT), '--host', '127.0.0.1'],
      {
        cwd: backendDir,
        stdio: 'pipe',
        env: { ...process.env, PYTHONPATH: backendDir },
      }
    )
  }

  backendProcess.stdout?.on('data', (data) => {
    logDev(`[Backend] ${data.toString().trim()}`)
  })

  backendProcess.stderr?.on('data', (data) => {
    errorDev(`[Backend] ${data.toString().trim()}`)
  })

  backendProcess.on('error', (error) => {
    errorDev(`[Backend] failed to start: ${error.message}`)
  })

  backendProcess.on('close', (code) => {
    logDev(`[Backend] exited with code ${code}`)
  })
}

function stopBackend() {
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
}

async function stopBackendTasks(timeoutMs = 2500) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/tasks/stop-all`, {
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
  stopBackend()
  mainWindow?.destroy()
  app.quit()
}

function createWindow() {
  Menu.setApplicationMenu(null)

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 900,
    minWidth: 1280,
    minHeight: 900,
    maxWidth: 1280,
    maxHeight: 900,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    autoHideMenuBar: true,
    title: 'VideoMatrix',
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
    titleBarStyle: 'hiddenInset',
  })

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

ipcMain.handle('app:getBackendPort', () => BACKEND_PORT)

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

  app.whenReady().then(() => {
    startBackend()
    // 等待后端启动
    setTimeout(createWindow, 1500)
  })

  app.on('window-all-closed', () => {
    if (!isQuitting) {
      void shutdownApp()
    }
    if (process.platform !== 'darwin') {
      app.quit()
    }
  })

  app.on('activate', () => {
    if (mainWindow === null) {
      createWindow()
    }
  })

  app.on('before-quit', () => {
    isQuitting = true
    stopBackend()
  })
}
