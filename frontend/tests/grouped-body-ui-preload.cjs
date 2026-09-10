localStorage.setItem('vm-config', JSON.stringify({
  hook_dir: 'hook', bgm_dir: 'bgm', body_dirs: ['normal-body'], body_mode: 'grouped', t_hook: 3,
  body_groups: [
    { enabled: true, folder: 'group-1', clip_count: 2, clip_duration: 2 },
    { enabled: true, folder: 'group-2', clip_count: 1, clip_duration: 2 },
  ],
  enable_random_cover: true,
}))
window.fetch = async (url) => new Response(JSON.stringify(
  String(url).endsWith('/health') ? { status: 'ok' } : [],
), { headers: { 'Content-Type': 'application/json' } })
