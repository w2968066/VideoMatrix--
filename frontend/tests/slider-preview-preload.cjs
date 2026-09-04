// Isolated renderer fixture: never read user settings or call the real backend.
localStorage.setItem('vm-config', JSON.stringify({
  enable_srt: true, srt_dir: 'test-subtitles', subtitle_y_percent: 72.5,
  subtitle_font_size_percent: 6.5, resolution: '1080*1920',
}))
window.fetch = async (url) => new Response(JSON.stringify(
  String(url).endsWith('/health') ? { status: 'ok' } : [],
), { headers: { 'Content-Type': 'application/json' } })
