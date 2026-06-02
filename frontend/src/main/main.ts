import { app, BrowserWindow, ipcMain, dialog, shell, Menu } from 'electron'
import { spawn, ChildProcess } from 'child_process'
import path from 'path'
import os from 'os'
import fs from 'fs'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let isQuitting = false

const isDev = process.env.NODE_ENV === 'development'
const BACKEND_PORT = 8765

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

// IPC 处理器
ipcMain.handle('dialog:openDirectory', async () => {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('dialog:openFile', async (_, filters) => {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: filters || [{ name: 'All Files', extensions: ['*'] }],
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('shell:openPath', async (_, filePath: string) => {
  await shell.openPath(filePath)
})

ipcMain.handle('app:getBackendPort', () => BACKEND_PORT)

// 应用生命周期
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
