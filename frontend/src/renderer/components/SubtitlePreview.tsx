interface SubtitlePreviewProps {
  resolution: string
  yPercent: number
  fontSizePercent: number
}

export function SubtitlePreview({ resolution, yPercent, fontSizePercent }: SubtitlePreviewProps) {
  const dimensions = resolution.toLowerCase().split(/[x*]/).map(Number)
  const valid = dimensions.length === 2 && dimensions.every((n) => Number.isFinite(n) && n > 0)
  const [width, height] = valid ? dimensions : [1080, 1920]
  const scale = Math.min(194 / width, 200 / height)
  const frameWidth = width * scale
  const frameHeight = height * scale
  // Match the renderer's ASS style quantization and top/center/bottom anchors.
  const centered = Math.abs(yPercent - 50) < 0.5
  const topAligned = yPercent < 50
  const margin = Math.round(288 * (topAligned ? yPercent : 100 - yPercent) / 100)
  const y = centered ? frameHeight / 2 : frameHeight * (topAligned ? margin / 288 : 1 - margin / 288)
  const fontSize = frameHeight * Math.round(288 * fontSizePercent / 100) / 288
  const sample = '这是十个字的字幕示例'
  const charsPerLine = Math.max(1, Math.floor(frameWidth * 0.9 / fontSize))
  const lines = sample.match(new RegExp(`.{1,${charsPerLine}}`, 'gu')) || [sample]
  const lineHeight = fontSize * 1.2
  const blockHeight = fontSize + (lines.length - 1) * lineHeight
  const textTop = centered ? y - blockHeight / 2 : topAligned ? y : y - blockHeight

  return (
    <div>
      <div className="mb-2 flex justify-between text-[11px]">
        <span>字幕示意</span>
        <span className="text-muted-foreground">非成品预览</span>
      </div>
      <div className="flex h-[200px] items-center justify-center">
        <svg
          data-preview-frame
          width={frameWidth}
          height={frameHeight}
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          className="rounded border border-white/20 bg-black"
        >
          <path d={`M${frameWidth / 2} 0V${frameHeight}`} stroke="white" strokeOpacity="0.12" strokeDasharray="3 4" />
          <path d={`M0 ${y}H${frameWidth}`} stroke="currentColor" className="text-accent" strokeOpacity="0.4" strokeDasharray="3 4" />
          <text
            data-preview-text
            x={frameWidth / 2}
            y={textTop}
            textAnchor="middle"
            dominantBaseline="text-before-edge"
            fontSize={fontSize}
            fill="white"
          >{lines.map((line, index) => (
            <tspan key={index} x={frameWidth / 2} y={textTop + index * lineHeight}>{line}</tspan>
          ))}</text>
        </svg>
      </div>
      <div className="mt-2 flex justify-between text-[10px] tabular-nums text-muted-foreground">
        <span>位置 {yPercent.toFixed(1)}%</span>
        <span>字号 {fontSizePercent.toFixed(1)}%</span>
      </div>
    </div>
  )
}
