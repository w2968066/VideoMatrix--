import { useEffect } from 'react'
import { useStore } from './store'
import { api } from './api/client'
import SinglePage from './components/SinglePage'
import Toast from './components/animation/Toast'

function App() {
  const { backendReady, setBackendReady, setTasks } = useStore()

  useEffect(() => {
    const check = async () => {
      try { await api.health(); setBackendReady(true) }
      catch { setTimeout(check, 1000) }
    }
    check()
  }, [setBackendReady])

  useEffect(() => {
    const interval = setInterval(async () => {
      try { const tasks = await api.listTasks(); setTasks(tasks) }
      catch {}
    }, 2000)
    return () => clearInterval(interval)
  }, [setTasks])

  if (!backendReady) {
    return (
      <div className="flex items-center justify-center w-screen h-screen bg-background">
        <div className="flex items-center gap-2.5 text-xs font-mono text-muted-foreground">
          <span className="w-2 h-2 bg-accent animate-pulse-dot" />
          正在启动后端服务
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col w-screen h-screen bg-background text-foreground overflow-hidden">
      <SinglePage />
      <Toast />
    </div>
  )
}

export default App
