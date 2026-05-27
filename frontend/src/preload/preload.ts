import { contextBridge, ipcRenderer } from 'electron'

export interface ElectronAPI {
  openDirectory: () => Promise<string | null>
  openFile: (filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  getBackendPort: () => Promise<number>
}

const api: ElectronAPI = {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile: (filters) => ipcRenderer.invoke('dialog:openFile', filters),
  openPath: (filePath) => ipcRenderer.invoke('shell:openPath', filePath),
  getBackendPort: () => ipcRenderer.invoke('app:getBackendPort'),
}

contextBridge.exposeInMainWorld('electronAPI', api)

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
