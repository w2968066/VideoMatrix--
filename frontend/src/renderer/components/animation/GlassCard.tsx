import React, { useState } from 'react'
import { glassCard } from '../../styles/theme'

interface GlassCardProps {
  children: React.ReactNode
  style?: React.CSSProperties
  hoverable?: boolean
  glowOnHover?: boolean
  onClick?: () => void
}

const GlassCard: React.FC<GlassCardProps> = ({
  children, style, hoverable = true, glowOnHover = false, onClick,
}) => {
  const [isHovered, setIsHovered] = useState(false)

  const hoverStyle = hoverable ? {
    transform: isHovered ? 'translateY(-4px) scale(1.005)' : 'translateY(0) scale(1)',
    boxShadow: isHovered
      ? '0 16px 48px rgba(0,0,0,0.5), 0 0 20px rgba(200,135,42,0.08), inset 0 1px 1px rgba(255,255,255,0.2)'
      : glassCard.boxShadow,
    borderColor: isHovered ? 'rgba(255,255,255,0.18)' : String(glassCard.border),
  } : {}

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        ...glassCard,
        ...hoverStyle,
        ...style,
        cursor: onClick ? 'pointer' : 'default',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* hover 时的流光效果 */}
      {glowOnHover && isHovered && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: 1,
            background: 'linear-gradient(90deg, transparent, rgba(200,135,42,0.4), transparent)',
            animation: 'shimmer 2s infinite',
          }}
        />
      )}
      {children}
    </div>
  )
}

export default GlassCard
