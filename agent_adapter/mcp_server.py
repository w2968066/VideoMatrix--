from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_adapter.client import AdapterError, VideoMatrixClient
from agent_adapter.preparation import VideoMatrixLauncher, build_prepare_update
from agent_adapter.profiles import available_profiles, build_config

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError as exc:
    raise SystemExit("缺少 MCP SDK。请执行：pip install -r agent_adapter/requirements.txt") from exc


mcp = MCPServer(
    "VideoMatrix",
    version="0.2.0",
    instructions=(
        "默认调用 prepare：只打开 VideoMatrix 并填写用户明确提供的路径和参数，不开始渲染。"
        "只有用户明确要求预检时调用 preflight；明确要求直接开始时才能调用 render 并传 confirm_start=true。"
    ),
)


def _client() -> VideoMatrixClient:
    return VideoMatrixClient(
        api_url=os.environ.get("VIDEOMATRIX_API_URL"),
        backend_exe=os.environ.get("VIDEOMATRIX_BACKEND_EXE"),
    )


def _config(
    profile: str,
    hook_dir: str,
    body_dir: str,
    bgm_path: str,
    output_dir: str,
    count: int,
    hook_seconds: float,
    body_seconds: float,
    clips: int,
    concurrency: int,
) -> dict[str, Any]:
    try:
        return build_config(
            profile=profile,
            hook_dir=hook_dir,
            body_dir=body_dir,
            bgm_path=bgm_path,
            output_dir=output_dir,
            count=count,
            hook_seconds=hook_seconds,
            body_seconds=body_seconds,
            clips=clips,
            concurrency=concurrency,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def videomatrix_profiles() -> list[dict[str, str]]:
    """List the two small adapter presets."""
    return available_profiles()


@mcp.tool()
def videomatrix_prepare(
    hook_dir: str = "",
    body_dir: str = "",
    bgm_path: str = "",
    voice_dir: str = "",
    subtitle_dir: str = "",
    watermark_path: str = "",
    output_dir: str = "",
    profile: str = "",
    use_defaults: bool = False,
    total_seconds: float | None = None,
    hook_seconds: float | None = None,
    body_seconds: float | None = None,
    clips: int | None = None,
    count: int | None = None,
    concurrency: int | None = None,
    resolution: str = "",
    fps: str = "",
    bitrate: str = "",
    hook_overlap: float | None = None,
    body_overlap: float | None = None,
    bgm_overlap: float | None = None,
) -> dict[str, Any]:
    """Open the GUI and merge only explicit paths/settings. Never starts rendering."""
    try:
        update = build_prepare_update(
            hook_dir=hook_dir,
            body_dir=body_dir,
            bgm_path=bgm_path,
            voice_dir=voice_dir,
            subtitle_dir=subtitle_dir,
            watermark_path=watermark_path,
            output_dir=output_dir,
            profile=profile,
            use_defaults=use_defaults,
            total_seconds=total_seconds,
            hook_seconds=hook_seconds,
            body_seconds=body_seconds,
            clips=clips,
            count=count,
            concurrency=concurrency,
            resolution=resolution,
            fps=fps,
            bitrate=bitrate,
            hook_overlap=hook_overlap,
            body_overlap=body_overlap,
            bgm_overlap=bgm_overlap,
        )
        return VideoMatrixLauncher().prepare(update)
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def videomatrix_preflight(
    hook_dir: str,
    body_dir: str = "",
    bgm_path: str = "",
    profile: str = "standard",
    output_dir: str = "",
    count: int = 10,
    hook_seconds: float = 3.0,
    body_seconds: float = 3.0,
    clips: int = 5,
    concurrency: int = 3,
) -> dict[str, Any]:
    """Estimate VideoMatrix capacity without starting a render."""
    try:
        return _client().preflight(
            _config(
                profile, hook_dir, body_dir, bgm_path, output_dir,
                count, hook_seconds, body_seconds, clips, concurrency,
            )
        )
    except AdapterError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def videomatrix_render(
    hook_dir: str,
    body_dir: str = "",
    bgm_path: str = "",
    profile: str = "standard",
    output_dir: str = "",
    count: int = 10,
    hook_seconds: float = 3.0,
    body_seconds: float = 3.0,
    clips: int = 5,
    concurrency: int = 3,
    confirm_start: bool = False,
) -> dict[str, Any]:
    """Start rendering only after the user explicitly requested it; confirm_start must be true."""
    try:
        if not confirm_start:
            raise ToolError("用户尚未明确授权开始渲染；请先使用 videomatrix_prepare。")
        client = _client()
        config = _config(
            profile, hook_dir, body_dir, bgm_path, output_dir,
            count, hook_seconds, body_seconds, clips, concurrency,
        )
        preflight = client.preflight(config)
        if not preflight["ok"]:
            return preflight
        result = client.create_task(config)
        result["capacity"] = preflight["capacity"]
        return result
    except AdapterError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def videomatrix_task(task_id: str, wait_seconds: int = 0) -> dict[str, Any]:
    """Read compact task status, or wait once until completion or timeout."""
    try:
        client = _client()
        if wait_seconds > 0:
            return client.wait_task(task_id, wait_seconds)
        return client.get_task(task_id)
    except AdapterError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def videomatrix_stop(task_id: str) -> dict[str, Any]:
    """Stop one VideoMatrix render task."""
    try:
        return _client().stop_task(task_id)
    except AdapterError as exc:
        raise ToolError(str(exc)) from exc


if __name__ == "__main__":
    mcp.run()
