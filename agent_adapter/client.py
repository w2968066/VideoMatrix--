from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://127.0.0.1:8765/api"
TERMINAL_STATES = {"completed", "failed", "stopped"}


class AdapterError(RuntimeError):
    pass


class VideoMatrixClient:
    def __init__(self, api_url: str | None = None, backend_exe: str | None = None):
        self.api_url = (api_url or os.environ.get("VIDEOMATRIX_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.auto_discover = not (api_url or os.environ.get("VIDEOMATRIX_API_URL"))
        self.backend_exe = backend_exe or os.environ.get("VIDEOMATRIX_BACKEND_EXE")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(f"{self.api_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except json.JSONDecodeError:
                detail = raw
            raise AdapterError(f"VideoMatrix API {exc.code}: {self._format_detail(detail)}") from exc
        except URLError as exc:
            raise AdapterError(f"无法连接 VideoMatrix 后端：{exc.reason}") from exc

    @staticmethod
    def _format_detail(detail: Any) -> str:
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            parts = []
            for item in detail[:5]:
                if isinstance(item, dict):
                    location = ".".join(str(value) for value in item.get("loc", []) if value != "body")
                    message = item.get("msg", "参数错误")
                    parts.append(f"{location}: {message}" if location else str(message))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        return json.dumps(detail, ensure_ascii=False, separators=(",", ":"))

    def ensure_backend(self, startup_timeout: float = 20.0) -> None:
        if self.auto_discover:
            runtime_file = Path(os.environ.get("APPDATA") or Path.home()) / "VideoMatrix" / "desktop-runtime.json"
            if runtime_file.is_file():
                try:
                    runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
                    port = runtime['port']
                    if not isinstance(port, int) or not 1 <= port <= 65535:
                        raise ValueError('invalid port')
                    self.api_url = f"http://127.0.0.1:{port}/api"
                    health = self._request("GET", "/health", timeout=1.5)
                    if not runtime.get('instance') or health.get('instance') != runtime['instance'] or health.get('version') != runtime.get('version'):
                        raise ValueError('backend identity mismatch')
                    return
                except (OSError, ValueError, KeyError, AdapterError) as exc:
                    raise AdapterError("桌面后端已退出或身份不匹配，请重新打开 VideoMatrix。") from exc
        try:
            self._request("GET", "/health", timeout=1.5)
            return
        except AdapterError:
            pass

        command, cwd = self._resolve_backend_command()
        parsed = urlparse(self.api_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise AdapterError("远程 API 不可用，适配器不会自动启动本地后端。")
        port = parsed.port or 8765
        command.extend(["--host", "127.0.0.1", "--port", str(port)])

        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            try:
                self._request("GET", "/health", timeout=1.0)
                return
            except AdapterError:
                time.sleep(0.4)
        raise AdapterError("VideoMatrix 后端启动超时。")

    def _resolve_backend_command(self) -> tuple[list[str], Path]:
        project_root = Path(__file__).resolve().parents[1]
        candidates: list[Path] = []
        if self.backend_exe:
            candidates.append(Path(self.backend_exe))
        candidates.extend(
            [
                project_root / "backend" / "dist" / "videomatrix-backend.exe",
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs"
                / "VideoMatrix"
                / "resources"
                / "backend"
                / "videomatrix-backend.exe",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return [str(candidate.resolve())], candidate.resolve().parent

        venv_python = project_root / "backend" / ".venv" / "Scripts" / "python.exe"
        backend_script = project_root / "backend" / "run_backend.py"
        if venv_python.is_file() and backend_script.is_file():
            return [str(venv_python), str(backend_script)], backend_script.parent

        raise AdapterError(
            "找不到 VideoMatrix 后端。请安装软件、先构建 backend/dist，"
            "或设置 VIDEOMATRIX_BACKEND_EXE。"
        )

    def health(self) -> dict[str, Any]:
        self.ensure_backend()
        result = self._request("GET", "/health", timeout=3.0)
        return {"ok": result.get("status") == "ok", "api": self.api_url}

    def preflight(self, config: dict[str, Any]) -> dict[str, Any]:
        self.ensure_backend()
        raw = self._request("POST", "/preflight", config, timeout=120.0)
        report = raw.get("report") or []
        failures = [
            {"name": item.get("name", ""), "message": item.get("message", "")}
            for item in report
            if not item.get("ok")
        ]
        return {
            "ok": bool(raw.get("ok")),
            "capacity": raw.get("capacity", 0),
            "libraries": len(report),
            "failures": failures[:5],
        }

    def create_task(self, config: dict[str, Any]) -> dict[str, Any]:
        self.ensure_backend()
        raw = self._request("POST", "/tasks", {"config": config}, timeout=30.0)
        return {"ok": True, "task_id": raw["task_id"], "status": "pending"}

    def get_task(self, task_id: str) -> dict[str, Any]:
        self.ensure_backend()
        raw = self._request("GET", f"/tasks/{task_id}", timeout=10.0)
        return self._summarize_task(raw)

    def wait_task(self, task_id: str, timeout_seconds: int = 600) -> dict[str, Any]:
        self.ensure_backend()
        deadline = time.monotonic() + max(1, timeout_seconds)
        while True:
            raw = self._request("GET", f"/tasks/{task_id}", timeout=10.0)
            if raw.get("status") in TERMINAL_STATES:
                return self._summarize_task(raw)
            if time.monotonic() >= deadline:
                summary = self._summarize_task(raw)
                summary["timed_out"] = True
                return summary
            time.sleep(1.0)

    def stop_task(self, task_id: str) -> dict[str, Any]:
        self.ensure_backend()
        self._request("POST", f"/tasks/{task_id}/stop", timeout=10.0)
        return {"ok": True, "task_id": task_id, "status": "stopping"}

    @staticmethod
    def _summarize_task(raw: dict[str, Any]) -> dict[str, Any]:
        output_files = raw.get("output_files") or []
        elapsed_values = [
            float(value)
            for value in (raw.get("output_elapsed") or {}).values()
            if isinstance(value, (int, float))
        ]
        status = str(raw.get("status", "unknown"))
        summary: dict[str, Any] = {
            "ok": status == "completed",
            "task_id": raw.get("task_id", ""),
            "status": status,
            "progress": raw.get("progress", 0),
            "completed": raw.get("current", 0),
            "total": raw.get("total", 0),
            "outputs": len(output_files),
        }
        if output_files:
            summary["output_dir"] = str(Path(output_files[0]).parent)
        if elapsed_values:
            summary["average_seconds"] = round(sum(elapsed_values) / len(elapsed_values), 2)
        if status in {"failed", "stopped"}:
            logs = raw.get("log_lines") or []
            summary["error"] = raw.get("message") or " | ".join(str(line) for line in logs[-3:])
        return summary
