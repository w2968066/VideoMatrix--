from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .client import AdapterError, VideoMatrixClient
from .preparation import VideoMatrixLauncher, build_prepare_update
from .profiles import available_profiles, build_config


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default="standard", help="standard 或 finished-hook")
    parser.add_argument("--hook", required=True, help="Hook 文件夹")
    parser.add_argument("--body", default="", help="Body 文件夹；省略时使用 Hook 文件夹")
    parser.add_argument("--bgm", default="", help="BGM 文件夹或带音轨的视频；省略时使用 Body 文件夹")
    parser.add_argument("--output", default="", help="输出目录；省略时沿用软件默认逻辑")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--hook-seconds", type=float, default=3.0)
    parser.add_argument("--body-seconds", type=float, default=3.0)
    parser.add_argument("--clips", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--resolution", default="1440*2560")
    parser.add_argument("--fps", default="24")
    parser.add_argument("--bitrate", default="8000k")
    parser.add_argument("--voice", default="")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--watermark", default="")
    parser.add_argument("--hook-overlap", type=float)
    parser.add_argument("--body-overlap", type=float)
    parser.add_argument("--bgm-overlap", type=float)
    parser.add_argument("--hook-volume", type=int)
    parser.add_argument("--body-volume", type=int)
    parser.add_argument("--bgm-volume", type=int)
    parser.add_argument("--voice-volume", type=int)
    parser.add_argument("--cpu", action="store_true", help="关闭 GPU 优先编码")


def _add_prepare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hook", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--bgm", default="")
    parser.add_argument("--voice", default="")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--watermark", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--profile", default="", help="省略则保留软件当前模式")
    parser.add_argument("--defaults", action="store_true", help="写入 Agent 推荐默认参数")
    parser.add_argument("--total-seconds", type=float)
    parser.add_argument("--hook-seconds", type=float)
    parser.add_argument("--body-seconds", type=float)
    parser.add_argument("--clips", type=int)
    parser.add_argument("--count", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--resolution", default="")
    parser.add_argument("--fps", default="")
    parser.add_argument("--bitrate", default="")
    parser.add_argument("--hook-overlap", type=float)
    parser.add_argument("--body-overlap", type=float)
    parser.add_argument("--bgm-overlap", type=float)


def _config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides = {
        "hook_r": args.hook_overlap,
        "body_r": args.body_overlap,
        "bgm_r": args.bgm_overlap,
        "vol_hook_orig": args.hook_volume,
        "vol_orig": args.body_volume,
        "vol_bgm": args.bgm_volume,
        "vol_voice": args.voice_volume,
    }
    return build_config(
        profile=args.profile,
        hook_dir=args.hook,
        body_dir=args.body,
        bgm_path=args.bgm,
        output_dir=args.output,
        count=args.count,
        hook_seconds=args.hook_seconds,
        body_seconds=args.body_seconds,
        clips=args.clips,
        concurrency=args.concurrency,
        resolution=args.resolution,
        fps=args.fps,
        bitrate=args.bitrate,
        voice_dir=args.voice,
        subtitle_dir=args.subtitle,
        watermark_path=args.watermark,
        gpu=not args.cpu,
        overrides=overrides,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="videomatrix-agent")
    parser.add_argument("--api", default=None, help="VideoMatrix API，例如 http://127.0.0.1:8765/api")
    parser.add_argument("--backend-exe", default=None, help="后端 exe 的绝对路径")
    parser.add_argument("--app-exe", default=None, help="VideoMatrix 桌面程序绝对路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="检查并按需启动后端")
    subparsers.add_parser("profiles", help="列出适配层预设")

    prepare_parser = subparsers.add_parser("prepare", help="打开软件并填写路径或参数，不开始渲染")
    _add_prepare_arguments(prepare_parser)

    preflight_parser = subparsers.add_parser("preflight", help="预检产能")
    _add_render_arguments(preflight_parser)

    render_parser = subparsers.add_parser("render", help="预检后创建任务并立即返回 task_id")
    _add_render_arguments(render_parser)

    run_parser = subparsers.add_parser("run", help="预检、创建任务并等待完成")
    _add_render_arguments(run_parser)
    run_parser.add_argument("--timeout", type=int, default=600)

    status_parser = subparsers.add_parser("status", help="读取精简任务状态")
    status_parser.add_argument("task_id")

    wait_parser = subparsers.add_parser("wait", help="等待任务完成，期间不输出日志")
    wait_parser.add_argument("task_id")
    wait_parser.add_argument("--timeout", type=int, default=600)

    stop_parser = subparsers.add_parser("stop", help="停止指定任务")
    stop_parser.add_argument("task_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = VideoMatrixClient(args.api, args.backend_exe)
    try:
        if args.command == "health":
            result = client.health()
        elif args.command == "profiles":
            result = {"ok": True, "profiles": available_profiles()}
        elif args.command == "prepare":
            update = build_prepare_update(
                hook_dir=args.hook,
                body_dir=args.body,
                bgm_path=args.bgm,
                voice_dir=args.voice,
                subtitle_dir=args.subtitle,
                watermark_path=args.watermark,
                output_dir=args.output,
                profile=args.profile,
                use_defaults=args.defaults,
                total_seconds=args.total_seconds,
                hook_seconds=args.hook_seconds,
                body_seconds=args.body_seconds,
                clips=args.clips,
                count=args.count,
                concurrency=args.concurrency,
                resolution=args.resolution,
                fps=args.fps,
                bitrate=args.bitrate,
                hook_overlap=args.hook_overlap,
                body_overlap=args.body_overlap,
                bgm_overlap=args.bgm_overlap,
            )
            result = VideoMatrixLauncher(args.app_exe).prepare(update)
        elif args.command in {"preflight", "render", "run"}:
            config = _config_from_args(args)
            preflight = client.preflight(config)
            if args.command == "preflight" or not preflight["ok"]:
                result = preflight
            else:
                result = client.create_task(config)
                result["capacity"] = preflight["capacity"]
                if args.command == "run":
                    result = client.wait_task(result["task_id"], args.timeout)
        elif args.command == "status":
            result = client.get_task(args.task_id)
        elif args.command == "wait":
            result = client.wait_task(args.task_id, args.timeout)
        elif args.command == "stop":
            result = client.stop_task(args.task_id)
        else:
            raise AdapterError(f"不支持的命令：{args.command}")
        print(_compact_json(result))
        return 0 if result.get("ok") or result.get("status") in {"pending", "running", "stopping"} else 2
    except (AdapterError, RuntimeError, ValueError) as exc:
        print(_compact_json({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
