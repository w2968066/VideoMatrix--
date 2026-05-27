from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime


class VideoConfig(BaseModel):
    task_name: str = Field(default="Task", description="任务名称")
    hook_dir: str = Field(..., description="首段素材目录")
    body_dirs: List[str] = Field(default_factory=list, description="后段素材目录列表")
    bgm_dir: str = Field(..., description="BGM 目录")
    voice_dir: Optional[str] = Field(default=None, description="配音目录")
    srt_dir: Optional[str] = Field(default=None, description="字幕目录")
    watermark_path: Optional[str] = Field(default=None, description="水印图片/GIF路径")
    base_out_dir: str = Field(..., description="输出父目录")
    
    t_hook: float = Field(default=3.0, ge=0.5, description="首段时长(秒)")
    t_body: float = Field(default=3.0, ge=0.5, description="后段片段时长(秒)")
    total_clips: int = Field(default=5, ge=2, description="每视频总片段数")
    target_count: int = Field(default=10, ge=1, description="目标生成数量")
    
    hook_r: float = Field(default=0.5, ge=0.0, le=0.99, description="首段重叠率")
    body_r: float = Field(default=0.5, ge=0.0, le=0.99, description="后段重叠率")
    bgm_r: float = Field(default=0.3, ge=0.0, le=0.99, description="BGM重叠率")
    
    resolution: str = Field(default="1080*1920", description="输出分辨率")
    fps: int = Field(default=30, ge=1, le=120, description="输出帧率")
    bitrate: str = Field(default="5000k", description="视频码率")
    
    vol_orig: int = Field(default=80, ge=0, le=200, description="原声音量(%)")
    vol_bgm: int = Field(default=30, ge=0, le=200, description="BGM音量(%)")
    vol_voice: int = Field(default=100, ge=0, le=200, description="配音音量(%)")
    
    enable_srt: bool = Field(default=False, description="是否启用硬字幕")
    enable_gpu: bool = Field(default=True, description="优先使用NVIDIA GPU编码")


class TaskStatus(BaseModel):
    task_id: str
    task_name: str
    status: Literal["pending", "running", "completed", "failed", "stopped"]
    progress: int = Field(default=0, ge=0, le=100)
    current: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    message: str = ""
    log_lines: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None
    output_files: List[str] = Field(default_factory=list)


class ProbeResult(BaseModel):
    file_path: str
    duration: float
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
