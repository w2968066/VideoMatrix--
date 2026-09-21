localStorage.setItem('vm-config', JSON.stringify({
  hook_dir: 'hook', body_dirs: ['body'], bgm_dir: 'bgm', srt_dir: 'subtitle',
  enable_srt: true, base_out_dir: 'output',
}))
window.fetch = async (url) => new Response(JSON.stringify(
  String(url).endsWith('/health') ? { status: 'ok' }
    : String(url).endsWith('/scan') ? { files: ['short.mp4', 'long.mp4'], count: 2 }
    : String(url).includes('/probe?') ? { source_duration: String(url).includes('short') ? 2 : 5 }
    : [],
), { headers: { 'Content-Type': 'application/json' } })
