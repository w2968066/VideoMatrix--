import { contextBridge, ipcRenderer } from 'electron'

export interface ElectronAPI {
  openDirectory: (defaultPath?: string, multi?: boolean) => Promise<string | string[] | null>
  openFile: (filters?: { name: string; extensions: string[] }[], defaultPath?: string) => Promise<string | null>
  saveTextFile: (defaultName: string, content: string, filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  getBackendPort: () => Promise<number>
}

const api: ElectronAPI = {
  openDirectory: (defaultPath, multi = false) => ipcRenderer.invoke('dialog:openDirectory', defaultPath, multi),
  openFile: (filters, defaultPath) => ipcRenderer.invoke('dialog:openFile', filters, defaultPath),
  saveTextFile: (defaultName, content, filters) => ipcRenderer.invoke('dialog:saveText', defaultName, content, filters),
  openPath: (filePath) => ipcRenderer.invoke('shell:openPath', filePath),
  getBackendPort: () => ipcRenderer.invoke('app:getBackendPort'),
}

contextBridge.exposeInMainWorld('electronAPI', api)

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
