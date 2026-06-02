// ==========================================
// 液态玻璃极简设计系统
// ==========================================

export const colors = {
  bg: '#07070A',
  bgElevated: '#0E0E14',

  text: { primary: '#E8E8F0', secondary: '#8A8A9A', muted: '#555560' },

  accent: '#C8872A',
  accentHover: '#D8993A',
  accentGlow: 'rgba(200,135,42,0.25)',

  success: '#4ADE80',
  error: '#F87171',

  glass: {
    bg: 'rgba(255,255,255,0.04)',
    bgHover: 'rgba(255,255,255,0.07)',
    border: 'rgba(255,255,255,0.08)',
    borderHover: 'rgba(255,255,255,0.14)',
    innerGlow: 'inset 0 1px 0 rgba(255,255,255,0.06)',
  },
}

export const glassCard: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.03) 100%)',
  backdropFilter: 'blur(24px) saturate(1.4)',
  WebkitBackdropFilter: 'blur(24px) saturate(1.4)',
  border: `1px solid ${colors.glass.border}`,
  borderRadius: 16,
  boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
}

export const glassInput: React.CSSProperties = {
  background: 'rgba(0,0,0,0.25)',
  border: `1px solid ${colors.glass.border}`,
  borderRadius: 8,
  color: colors.text.primary,
  fontSize: 13,
  padding: '8px 12px',
  outline: 'none',
  width: '100%',
  transition: 'border-color 0.2s ease',
}

export const glassBtn: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)',
  border: `1px solid ${colors.glass.border}`,
  borderRadius: 8,
  color: colors.text.secondary,
  fontSize: 13,
  padding: '8px 16px',
  cursor: 'pointer',
  backdropFilter: 'blur(8px)',
  transition: 'all 0.2s ease',
}

export const primaryBtn: React.CSSProperties = {
  background: colors.accent,
  border: 'none',
  borderRadius: 8,
  color: '#07070A',
  fontSize: 14,
  fontWeight: 600,
  padding: '10px 24px',
  cursor: 'pointer',
  transition: 'all 0.2s ease',
}
