"""Plan three independent music tracks against the actual output timeline."""
import math

TRACK_LABELS = {'full': '全片 BGM', 'hook': 'Hook BGM', 'body': 'Body BGM'}


def configured_tracks(config):
    return config.get('bgm_tracks') or {}


def prepare_track(scope, settings, clip, hook_duration, total_duration, audio_mode, rng):
    offset = hook_duration if scope == 'body' else 0.0
    interval = hook_duration if scope == 'hook' else total_duration - offset
    if interval <= 1e-8 or float(settings['volume']) <= 0:
        return None
    source_duration = float(clip['duration'])
    master = scope == 'full' and audio_mode
    start = 0.0
    if not master and settings.get('source_mode') == 'random' and source_duration > interval:
        step = max(interval * (1 - float(settings.get('overlap', .3))), .1)
        start = rng.randrange(math.floor((source_duration - interval) / step) + 1) * step
    available = source_duration - start
    looping = not master and settings.get('short_behavior', 'loop') == 'loop' and available < interval - 1e-8
    duration = interval if looping else min(interval, available)
    fade_in, fade_out = float(settings.get('fade_in', 0)), float(settings.get('fade_out', 0))
    scale = min(1.0, duration / (fade_in + fade_out)) if fade_in + fade_out else 1.0
    return dict(scope=scope, file=clip['file'], start=start, source_duration=source_duration,
                duration=duration, interval=interval, offset=offset, loop=looping,
                seek=max(0.0, start - 1.0),
                volume=float(settings['volume']) / 100, fade_in=fade_in * scale,
                fade_out=fade_out * scale, fade_adjusted=scale < 1)


def track_filter(track, input_index):
    # Loop audio samples, not container timestamps: a video may have an audio
    # track shorter than its picture. Only short, actually looped sources need
    # a buffer. Fades apply once to the entire audible interval.
    chain = (f"[{input_index}:a]atrim=start={track['start'] - track.get('seek', 0):.9f},asetpts=PTS-STARTPTS,"
             "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo")
    if track['loop']:
        sample_count = max(1, round((track['source_duration'] - track['start']) * 44100))
        chain += f",atrim=duration={track['source_duration'] - track['start']:.9f},aloop=loop=-1:size={sample_count}"
    chain += f",atrim=duration={track['duration']:.9f},asetpts=PTS-STARTPTS,volume={track['volume']:.9f}"
    if track['fade_in']:
        chain += f",afade=t=in:st=0:d={track['fade_in']:.9f}"
    if track['fade_out']:
        chain += f",afade=t=out:st={max(0, track['duration'] - track['fade_out']):.9f}:d={track['fade_out']:.9f}"
    if track['offset']:
        # Sample-based delay keeps the Body boundary accurate at fractional fps.
        delay = round(track['offset'] * 44100)
        chain += f",adelay={delay}S:all=1"
    return chain + f"[bgm_{track['scope']}]; "
