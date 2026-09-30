import { contextBridge, ipcRenderer } from 'electron'
import type { DirectoryListing, DirectoryAction, TrashResult } from '../shared/directory'

export interface ElectronAPI {
  directoryActionMenu: (parent: string, items: string[]) => Promise<DirectoryAction | null>
  renameEntry: (parent: string, item: string, name: string) => Promise<string>
  trashEntries: (parent: string, items: string[]) => Promise<TrashResult>
  openEntry: (parent: string, item: string) => Promise<void>
  revealEntry: (parent: string, item: string) => Promise<void>
  copyEntryPaths: (parent: string, items: string[]) => Promise<void>
  createDirectory: (parent: string, name: string) => Promise<string>
  readThumbnail: (file: string) => Promise<string | null>
  readDirectory: (directory?: string) => Promise<DirectoryListing>
  openDirectory: (defaultPath?: string, multi?: boolean) => Promise<string | string[] | null>
  openFile: (filters?: { name: string; extensions: string[] }[], defaultPath?: string) => Promise<string | null>
  saveTextFile: (defaultName: string, content: string, filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  getBackendPort: () => Promise<number>
}

const api: ElectronAPI = {
  directoryActionMenu: (parent, items) => ipcRenderer.invoke('directory:menu', parent, items),
  renameEntry: (parent, item, name) => ipcRenderer.invoke('directory:rename', parent, item, name),
  trashEntries: (parent, items) => ipcRenderer.invoke('directory:trash', parent, items),
  openEntry: (parent, item) => ipcRenderer.invoke('directory:open', parent, item),
  revealEntry: (parent, item) => ipcRenderer.invoke('directory:reveal', parent, item),
  copyEntryPaths: (parent, items) => ipcRenderer.invoke('directory:copy', parent, items),
  createDirectory: (parent, name) => ipcRenderer.invoke('directory:create', parent, name),
  readThumbnail: (file) => ipcRenderer.invoke('directory:thumbnail', file),
  readDirectory: (directory) => ipcRenderer.invoke('directory:list', directory),
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
