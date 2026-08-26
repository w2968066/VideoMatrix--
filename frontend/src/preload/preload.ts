import { contextBridge, ipcRenderer } from 'electron'

export interface ElectronAPI {
  openDirectory: (defaultPath?: string) => Promise<string | null>
  openFile: (filters?: { name: string; extensions: string[] }[], defaultPath?: string) => Promise<string | null>
  saveTextFile: (defaultName: string, content: string, filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  getBackendPort: () => Promise<number>
}

const api: ElectronAPI = {
  openDirectory: (defaultPath) => ipcRenderer.invoke('dialog:openDirectory', defaultPath),
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
