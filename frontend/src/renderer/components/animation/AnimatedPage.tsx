import React, { useRef, useEffect, useState } from 'react'

interface AnimatedPageProps {
  children: React.ReactNode
  pageKey: string
}

const AnimatedPage: React.FC<AnimatedPageProps> = ({ children, pageKey }) => {
  const [isVisible, setIsVisible] = useState(false)
  const prevKey = useRef(pageKey)

  useEffect(() => {
    if (prevKey.current !== pageKey) {
      setIsVisible(false)
      const timer = setTimeout(() => setIsVisible(true), 30)
      prevKey.current = pageKey
      return () => clearTimeout(timer)
    } else {
      setIsVisible(true)
    }
  }, [pageKey])

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        opacity: isVisible ? 1 : 0,
        transform: isVisible ? 'translateY(0) scale(1)' : 'translateY(12px) scale(0.98)',
        transition: 'opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1), transform 0.35s cubic-bezier(0.4, 0, 0.2, 1)',
        willChange: 'opacity, transform',
      }}
    >
      {children}
    </div>
  )
}

export default AnimatedPage
