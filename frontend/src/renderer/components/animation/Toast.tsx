import React, { useEffect, useRef } from 'react'
import { useStore } from '../../store'

export interface ToastItem {
  id: string
  message: string
  type: 'success' | 'error' | 'warning' | 'info'
}

const Toast: React.FC = () => {
  const { toasts, removeToast } = useStore()
  return (
    <div className="fixed top-5 left-1/2 -translate-x-1/2 z-[1000] flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <ToastItemComponent
          key={toast.id}
          toast={toast}
          onRemove={removeToast}
        />
      ))}
    </div>
  )
}

const ToastItemComponent: React.FC<{
  toast: ToastItem
  onRemove: (id: string) => void
}> = ({ toast, onRemove }) => {
  const [exiting, setExiting] = React.useState(false)

  // Keep latest onRemove in a ref so the auto-dismiss timer can stay on a
  // stable, empty-deps effect — otherwise a new closure on every parent
  // re-render (e.g. the 2 s task poll) keeps resetting the timer and the
  // toast never disappears.
  const onRemoveRef = useRef(onRemove)
  useEffect(() => { onRemoveRef.current = onRemove }, [onRemove])

  useEffect(() => {
    const exitTimer = setTimeout(() => {
      setExiting(true)
      setTimeout(() => onRemoveRef.current(toast.id), 380)
    }, 3000)
    return () => clearTimeout(exitTimer)
  }, [toast.id])

  const dot = {
    success: 'bg-ok',
    error: 'bg-hot',
    warning: 'bg-accent',
    info: 'bg-blue-400',
  }[toast.type]

  return (
    <div
      className="bg-[#0E0E14] border border-white/[0.10] px-4 py-2.5 min-w-[240px] flex items-center gap-3 shadow-[0_12px_32px_-12px_rgba(0,0,0,0.8)]"
      style={{
        animation: exiting
          ? 'toastOut 0.38s cubic-bezier(0.4,0,1,1) forwards'
          : 'toastIn 0.4s cubic-bezier(0.22,1,0.36,1) forwards',
      }}
    >
      <span className={`w-1.5 h-1.5 ${dot}`} />
      <span className="text-[12px] text-foreground/90">{toast.message}</span>
    </div>
  )
}

export default Toast
