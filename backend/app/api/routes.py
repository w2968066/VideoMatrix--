import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
import json

from ..models.schemas import (
    CreateTaskRequest, CreateTaskResponse, StopTaskRequest,
    TaskStatus, ScanRequest, ScanResponse, ProbeResult, VideoConfig
)
from ..services.task_service import task_service
from ..core.ffmpeg import probe_media, extract_media_info

router = APIRouter()


@router.post("/tasks", response_model=CreateTaskResponse)
def create_task(req: CreateTaskRequest):
    task_id = task_service.create_task(req.config)
    return CreateTaskResponse(task_id=task_id, message="任务已创建")


@router.get("/tasks", response_model=list[TaskStatus])
def list_tasks():
    return task_service.get_all_tasks()


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str):
    success = task_service.stop_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "停止指令已发送"}


@router.get("/tasks/{task_id}/logs")
def get_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"logs": task_service.get_logs(task_id)}


@router.get("/tasks/{task_id}/stream")
async def stream_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        last_len = 0
        while True:
            logs = task_service.get_logs(task_id)
            if len(logs) > last_len:
                new_logs = logs[last_len:]
                for line in new_logs:
                    yield f"data: {json.dumps({'log': line}, ensure_ascii=False)}\n\n"
                last_len = len(logs)

            current = task_service.get_task(task_id)
            if current and current.status in ("completed", "failed", "stopped"):
                yield f"data: {json.dumps({'status': current.status, 'progress': current.progress}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/scan", response_model=ScanResponse)
def scan_directory(req: ScanRequest):
    if not os.path.exists(req.dir_path):
        raise HTTPException(status_code=400, detail="目录不存在")
    files = []
    for root, _, filenames in os.walk(req.dir_path):
        for f in filenames:
            if any(f.lower().endswith(ext) for ext in req.extensions):
                files.append(os.path.join(root, f))
    return ScanResponse(files=files, count=len(files))


@router.post("/probe", response_model=ProbeResult)
def probe_file(file_path: str):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="文件不存在")
    info = probe_media(file_path)
    if not info:
        raise HTTPException(status_code=500, detail="无法探测文件")
    dur, has_audio, width, height, fps = extract_media_info(info, file_path)
    return ProbeResult(
        file_path=file_path,
        duration=dur,
        has_audio=has_audio,
        width=width,
        height=height,
        fps=fps
    )


@router.post("/benchmark")
def benchmark(config: VideoConfig):
    return task_service.get_benchmark(config)


@router.post("/preflight")
def preflight(config: VideoConfig):
    return task_service.preflight(config)


@router.post("/history/clear")
def clear_history():
    task_service.clear_history()
    return {"message": "使用记录已清除"}
