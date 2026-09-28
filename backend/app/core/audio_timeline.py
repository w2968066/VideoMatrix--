"""Plan ordered Body rounds without reusing candidate slices in one output."""
from .timeline import body_segment_specs


def fit_body_to_audio(config, pools, hook_duration, audio_duration, rng):
    target = max(0.0, audio_duration - hook_duration) if config.get('apply_bgm_to_hook', True) else audio_duration
    remaining = target
    specs = body_segment_specs(config)
    available = [list(pool) for pool in pools]
    clips = []
    if remaining > 1e-8 and not specs:
        raise ValueError('按 BGM 时长生成需要至少一个 Body 分组。')
    while remaining > 1e-8:
        before = remaining
        for spec, pool in zip(specs, available):
            for _ in range(spec['clip_count']):
                if remaining <= 1e-8:
                    break
                if not pool:
                    raise ValueError(f"Body 分组 {spec['group_index'] or 1} 素材不足以补满 BGM；请增加素材或调整裁切设置。")
                # Preflight uses shortest-first to check the most demanding group cycle.
                index = rng.randrange(len(pool)) if rng is not None else min(range(len(pool)), key=lambda i: pool[i]['duration'])
                clip = dict(pool.pop(index))
                clip['duration'] = min(float(clip['duration']), remaining)
                clips.append(clip)
                remaining -= clip['duration']
        if remaining >= before:
            raise ValueError('Body 片段数或时长无效，无法补满 BGM。')
    return clips
