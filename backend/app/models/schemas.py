from pydantic import BaseModel, Field, model_validator
from typing import Dict, List, Optional, Literal, Union
from datetime import datetime


class BgmTrack(BaseModel):
    enabled: bool = True
    path: str = ""
    volume: float = Field(default=30, ge=0, le=200)
    fade_in: float = Field(default=0, ge=0, le=3600)
    fade_out: float = Field(default=0, ge=0, le=3600)
    short_behavior: Literal["loop", "stop"] = "loop"
    source_mode: Literal["start", "random"] = "start"
    overlap: float = Field(default=0.3, ge=0, le=1)


class BgmTracks(BaseModel):
    full: BgmTrack = Field(default_factory=BgmTrack)
    hook: BgmTrack = Field(default_factory=lambda: BgmTrack(enabled=False))
    body: BgmTrack = Field(default_factory=lambda: BgmTrack(enabled=False))


class VideoConfig(BaseModel):
    task_name: str = Field(default="Task", description="任务名称")
    hook_dir: str = Field(..., description="首段素材目录")
    body_dirs: List[str] = Field(default_factory=list, description="后段素材目录列表")
    body_mode: Literal["normal", "grouped"] = Field(default="normal", description="后段拼接模式")
    body_groups: List["BodyGroup"] = Field(default_factory=list, max_length=4, description="按顺序拼接的 Body 分组")
    bgm_dir: str = Field(default="", description="可选 BGM 目录或带音轨的视频文件")
    bgm_tracks: Optional[BgmTracks] = Field(default=None, description="全片 / Hook / Body 三条独立配乐；未提供时兼容旧接口")
    duration_mode: Literal["clips", "bgm"] = Field(default="clips", description="按片段数量或完整 BGM 时长生成")
    voice_dir: Optional[str] = Field(default=None, description="配音目录")
    srt_dir: Optional[str] = Field(default=None, description="字幕目录")
    watermark_path: Optional[str] = Field(default=None, description="水印图片/GIF路径")
    base_out_dir: str = Field(default="", description="输出父目录")
    
    t_hook: float = Field(default=3.0, ge=0.5, description="首段时长(秒)")
    hook_full_duration: bool = Field(default=False, description="按原素材时长使用完整 Hook，随机轮换")
    t_body: float = Field(default=3.0, ge=0.5, description="后段片段时长(秒)")
    body_full_duration: bool = Field(default=False, description="普通 Body 使用完整原素材")
    total_clips: int = Field(default=5, ge=2, description="每视频总片段数")
    target_count: int = Field(default=10, ge=1, description="目标生成数量")
    
    hook_r: float = Field(default=0.5, ge=0.0, le=1.0, description="首段重叠率")
    body_r: float = Field(default=0.5, ge=0.0, le=1.0, description="后段重叠率")
    bgm_r: float = Field(default=0.3, ge=0.0, le=1.0, description="BGM重叠率")
    
    resolution: str = Field(default="1080*1920", description="输出分辨率")
    fps: Union[str, float, int] = Field(default="30", description="输出帧率")
    bitrate: str = Field(default="8000k", description="视频码率")
    random_resolution_enabled: bool = Field(default=False, description="主输出随机分辨率，保持当前宽高比")
    random_resolution_min: int = Field(default=1080, description="随机分辨率短边下限(px)")
    random_resolution_max: int = Field(default=1440, description="随机分辨率短边上限(px)")
    random_bitrate_enabled: bool = Field(default=False, description="主输出随机视频目标码率")
    random_bitrate_min: int = Field(default=8000, description="随机视频目标码率下限(kbps)")
    random_bitrate_max: int = Field(default=14000, description="随机视频目标码率上限(kbps)")
    
    vol_orig: int = Field(default=80, ge=0, le=200, description="Body原声音量(%)")
    vol_hook_orig: Optional[int] = Field(default=None, ge=0, le=200, description="Hook原声音量(%)")
    vol_bgm: int = Field(default=30, ge=0, le=200, description="BGM音量(%)")
    vol_voice: int = Field(default=100, ge=0, le=200, description="配音音量(%)")

    apply_bgm_to_hook: bool = Field(default=True, description="BGM是否作用于Hook")
    apply_voice_to_hook: bool = Field(default=True, description="配音是否作用于Hook")
    apply_srt_to_hook: bool = Field(default=True, description="字幕是否作用于Hook")
    apply_watermark_to_hook: bool = Field(default=True, description="水印是否作用于Hook")
    
    enable_srt: bool = Field(default=False, description="是否启用硬字幕")
    subtitle_y_percent: float = Field(default=92.0, ge=8.0, le=92.0, description="字幕垂直位置百分比")
    subtitle_font_size_percent: float = Field(default=5.6, ge=3.0, le=9.0, description="字幕字号占画面高度百分比")
    enable_gpu: bool = Field(default=True, description="自动选择可用硬件编码；不可用时继续使用CPU")
    concurrent_tasks: int = Field(default=3, ge=1, le=16, description="并发渲染数")

    enable_variants: bool = Field(default=False, description="启用独立画面变换层，与混剪共用一次编码")
    variant_strength: Literal["mild", "balanced", "strong"] = Field(default="balanced", description="变体强度")
    variant_hook: bool = Field(default=True, description="变体作用于 Hook")
    variant_body: bool = Field(default=True, description="变体作用于 Body")
    variant_mirror: bool = Field(default=False, description="允许随机镜像")
    variant_crop: bool = Field(default=True, description="微裁切，按强度最多裁去每方向两侧合计 2%")
    variant_color: bool = Field(default=False, description="显式启用轻微色彩调整")
    variant_frame_mix: bool = Field(default=False, description="旧配置兼容字段，V2 不再执行帧混合")
    variant_blend_enabled: bool = Field(default=False, description="显式启用 B 画面混合，会增加解码与滤镜耗时")
    variant_blend_path: str = ""
    variant_blend_opacity: float = Field(default=0.03, ge=0.01, le=0.15)
    variant_blend_eof: Literal["loop", "freeze", "error"] = "loop"
    variant_protected_regions: List[dict] = Field(default_factory=list, max_length=64, description="基础画面归一化保护区域，含 x/y/width/height 及可选 start/end")
    variant_seed: Optional[int] = Field(default=None, ge=0, description="任务级随机种子")
    enable_random_cover: bool = Field(default=False, description="随机替换成片首帧")
    random_cover_mode: Literal["replace", "insert"] = Field(default="replace", description="随机封面首帧处理方式")

    @model_validator(mode='before')
    @classmethod
    def ignore_inactive_output_ranges(cls, values):
        if isinstance(values, dict):
            from ..core.output_settings import OUTPUT_DEFAULTS
            values = values.copy()
            for prefix in ('random_resolution', 'random_bitrate'):
                flag = values.get(f'{prefix}_enabled', False)
                inactive = flag is None or flag is False or flag == 0 or (
                    isinstance(flag, str) and flag.lower() in ('false', 'f', '0', 'off', 'no', 'n')
                )
                if inactive:
                    for suffix in ('min', 'max'):
                        key = f'{prefix}_{suffix}'
                        values[key] = OUTPUT_DEFAULTS[key]
        return values

    @model_validator(mode='after')
    def check_output_ranges(self):
        from ..core.output_settings import validate_output_settings
        validate_output_settings(self.model_dump())
        return self


class BodyGroup(BaseModel):
    full_duration: bool = Field(default=False, description="本组使用完整原素材")
    enabled: bool = Field(default=True, description="是否使用此组")
    folder: str = Field(default="", description="此组素材目录")
    clip_count: int = Field(default=1, ge=1, description="此组抽取片段数")
    clip_duration: float = Field(default=3.0, ge=0.5, description="此组片段时长(秒)")


class TaskStatus(BaseModel):
    task_id: str
    task_name: str
    status: Literal["pending", "running", "completed", "partial", "failed", "stopped"]
    progress: int = Field(default=0, ge=0, le=100)
    current: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    message: str = ""
    log_lines: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None
    output_files: List[str] = Field(default_factory=list)
    output_elapsed: Dict[str, float] = Field(default_factory=dict)
    output_warnings: Dict[str, str] = Field(default_factory=dict)
    acceleration: str = ""
    acceleration_warning: str = ""
    effective_concurrency: int = 0


class ProbeResult(BaseModel):
    file_path: str
    duration: float
    source_duration: float = 0
    audio_duration: float = 0
    has_audio: bool
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None


class ScanRequest(BaseModel):
    dir_path: str
    extensions: List[str] = Field(default_factory=lambda: [".mp4", ".mov"])


class ScanResponse(BaseModel):
    files: List[str]
    count: int


class CreateTaskRequest(BaseModel):
    config: VideoConfig


class CreateTaskResponse(BaseModel):
    task_id: str
    message: str


class StopTaskRequest(BaseModel):
    task_id: str
