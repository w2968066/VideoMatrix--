import React from 'react'

interface StaggerContainerProps {
  children: React.ReactNode
  staggerDelay?: number
  baseDelay?: number
}

export const StaggerContainer: React.FC<StaggerContainerProps> = ({
  children,
  staggerDelay = 0.06,
  baseDelay = 0.1,
}) => {
  return (
    <div style={{ width: '100%', height: '100%' }}>
      {React.Children.map(children, (child, i) => (
        <StaggerItem key={i} delay={baseDelay + i * staggerDelay}>
          {child}
        </StaggerItem>
      ))}
    </div>
  )
}

const StaggerItem: React.FC<{ children: React.ReactNode; delay: number }> = ({
  children, delay,
}) => {
  const [show, setShow] = React.useState(false)

  React.useEffect(() => {
    const timer = setTimeout(() => setShow(true), delay * 1000)
    return () => clearTimeout(timer)
  }, [delay])

  return (
    <div
      style={{
        opacity: show ? 1 : 0,
        transform: show ? 'translateY(0)' : 'translateY(20px)',
        transition: `opacity 0.45s cubic-bezier(0.4, 0, 0.2, 1) ${delay}s, transform 0.45s cubic-bezier(0.4, 0, 0.2, 1) ${delay}s`,
        willChange: 'opacity, transform',
      }}
    >
      {children}
    </div>
  )
}

export default StaggerContainer
