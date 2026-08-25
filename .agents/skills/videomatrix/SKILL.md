---
name: videomatrix
description: Prepare, preflight, render, monitor, or stop local VideoMatrix Hook/Body mixing jobs. Trigger for 混剪、批量视频、Hook、Body、成品 Hook or VideoMatrix requests; do not use for manual timeline editing.
---

# VideoMatrix

Prefer the `videomatrix_*` MCP tools. If unavailable, run `python -m agent_adapter` from the repository root.

- Default to `videomatrix_prepare`: identify only the path roles evident from context, open the GUI, and merge those fields into the user's saved settings. Do not guess an ambiguous Hook, Body, BGM, voice, subtitle, watermark, or output role.
- Preserve saved settings unless the user asks for defaults, a named mode, or explicit parameter changes. Set `use_defaults=true` only for a new/default setup; it applies Hook/Body/BGM overlap `0/0.2/0.1`, vertical 2K `1440*2560`, `8000k`, and `24fps`.
- Use `finished-hook` only when the Hook already contains finished audio, subtitles, or watermark treatment. Omit `profile` to preserve the current mode.
- A total clip count includes the Hook. The formula is `total = hook + body * (clips - 1)`. When given total duration and Hook duration, pass both to `prepare`; it derives an exact Body duration and clip count. If total duration is given without Hook duration, ask for it.
- Use `videomatrix_preflight` only when the user explicitly asks to preflight or estimate capacity.
- Rendering writes files. Call `videomatrix_render` with `confirm_start=true` only when the user explicitly asks to start, generate, or render. A general request to "混剪" is not start authorization.
- After render returns a task id, call `videomatrix_task` once with a suitable `wait_seconds`; do not repeatedly poll or request full logs.
- Report only status, output count, output directory, and average render time. Preserve the adapter's compact errors when a preflight fails.
- Do not clear usage history; the adapter intentionally exposes no history-clear tool.
