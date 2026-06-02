import os
import json
import time
import uuid
import threading
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Callable

from ..core.video_matrix import VideoMatrixCore, SharedMediaCache
from ..models.schemas import VideoConfig, TaskStatus


class TaskService:
    def __init__(self):
        self.shared_cache = SharedMediaCache()
        self.tasks: Dict[str, TaskStatus] = {}
        self.active_cores: Dict[str, List[VideoMatrixCore]] = {}
        self.cores_lock = threading.Lock()
        self.log_buffers: Dict[str, List[str]] = {}
        self._log_lock = threading.Lock()

    def _create_log_callback(self, task_id: str) -> Callable:
        def callback(message: str):
            with self._log_lock:
                if task_id not in self.log_buffers:
                    self.log_buffers[task_id] = []
                self.log_buffers[task_id].append(message)
            if task_id in self.tasks:
                self.tasks[task_id].log_lines.append(message)
        return callback

    def _normalize_config(self, config: dict) -> dict:
        normalized = config.copy()
        body_dirs = normalized.get('body_dirs') or []
        if isinstance(body_dirs, str):
            body_dirs = [p.strip() for p in body_dirs.split(';') if p.strip()]
        normalized['body_dirs'] = body_dirs

        if not normalized.get('base_out_dir', '').strip():
            h_dir = normalized.get('hook_dir', '').rstrip('/\\')
            parent = os.path.dirname(h_dir) or h_dir
            name = os.path.basename(h_dir) or "VideoMatrix"
            normalized['base_out_dir'] = os.path.join(parent, f"{name}_VideoMatrix_Output")

        return normalized

    def _get_tasks_from_config(self, config: dict) -> List[dict]:
        tasks = []
        h_dir = config['hook_dir'].strip()
        body_dirs = config.get('body_dirs') or [h_dir]
        b_dir = ';'.join(body_dirs)
        out_base = config['base_out_dir'].strip()
        sub_folders = [f for f in os.listdir(h_dir) if os.path.isdir(os.path.join(h_dir, f))]
        if sub_folders:
            for sub in sub_folders:
                task_h = os.path.join(h_dir, sub)
                if b_dir == h_dir:
                    task_b = [os.path.join(h_dir, sub)]
                else:
                    potential_b_sub = os.path.join(b_dir, sub)
                    if os.path.isdir(potential_b_sub):
                        task_b = [potential_b_sub]
                    else:
                        task_b = body_dirs
                tasks.append({
                    'name': sub,
                    'hook_dir': task_h,
                    'body_dirs': task_b,
                    'out': os.path.join(out_base, sub)
                })
        else:
            task_name = os.path.basename(h_dir.rstrip('/\\')) or "Single_Task"
            tasks.append({
                'name': task_name,
                'hook_dir': h_dir,
                'body_dirs': body_dirs,
                'out': os.path.join(out_base, task_name)
            })
        return tasks

    def create_task(self, config: VideoConfig) -> str:
        task_id = str(uuid.uuid4())
        raw_config = self._normalize_config(config.model_dump())
        task_cfg = raw_config.copy()

        status = TaskStatus(
            task_id=task_id,
            task_name=task_cfg.get('task_name', 'Task'),
            status="pending",
            created_at=datetime.now()
        )
        self.tasks[task_id] = status
        self.log_buffers[task_id] = []

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task_id, task_cfg),
            daemon=True
        )
        thread.start()
        return task_id

    def _run_pipeline(self, task_id: str, config: dict):
        status = self.tasks[task_id]
        log_cb = self._create_log_callback(task_id)
        status.status = "running"
        status.updated_at = datetime.now()

        try:
            tasks = self._get_tasks_from_config(config)
            if not tasks:
                log_cb(">>> [错误] 找不到任何素材目录！")
                status.status = "failed"
                status.message = "找不到素材目录"
                return

            cores = []
            for t in tasks:
                if status.status == "stopped":
                    break
                task_cfg = config.copy()
                task_cfg['task_name'] = t['name']
                task_cfg['hook_dir'] = t['hook_dir']
                task_cfg['body_dirs'] = t['body_dirs']
                task_cfg['out_dir'] = t['out']
                os.makedirs(task_cfg['out_dir'], exist_ok=True)

                core = VideoMatrixCore(task_cfg, log_cb, self.shared_cache)
                ok, msg = core.pre_flight_check()
                if ok:
                    cores.append(core)
                    with self.cores_lock:
                        if task_id not in self.active_cores:
                            self.active_cores[task_id] = []
                        self.active_cores[task_id].append(core)
                else:
                    log_cb(f"[{t['name']}] 预检拦截: {msg}")

            if not cores:
                log_cb(">>> 所有库均被拦截，任务终止。")
                status.status = "failed"
                status.message = "所有库预检失败"
                return

            jobs = []
            max_t = max([c.config['target_count'] for c in cores], default=0)
            for i in range(1, max_t + 1):
                for core in cores:
                    if i <= core.config['target_count']:
                        jobs.append((core, i))

            status.total = len(jobs)
            log_cb(f">>> [系统] 任务池组装完毕，即将并线生成 {len(jobs)} 个视频。")

            success_counts = {core.task_name: 0 for core in cores}
            completed = 0

            concurrent_limit = config.get('concurrent_tasks', 3)
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_limit) as executor:
                futures = {executor.submit(self._render_job, core, idx, status): core for core, idx in jobs}
                for future in concurrent.futures.as_completed(futures):
                    if status.status == "stopped":
                        break
                    core = futures[future]
                    try:
                        if future.result():
                            success_counts[core.task_name] += 1
                    except Exception as e:
                        log_cb(f"[{core.task_name}] 渲染异常: {e}")
                    completed += 1
                    status.current = completed
                    status.progress = int(completed / len(jobs) * 100)
                    status.updated_at = datetime.now()

            if status.status != "stopped":
                status.status = "completed"
                log_cb(">>> [完成] 渲染任务队列执行完毕！")
                for core_name, cnt in success_counts.items():
                    target = next(c.config['target_count'] for c in cores if c.task_name == core_name)
                    log_cb(f"    [{core_name}] 最终产量: {cnt}/{target}")

        except Exception as e:
            log_cb(f"[严重错误] 调度引擎崩溃: {str(e)}")
            status.status = "failed"
            status.message = str(e)
        finally:
            status.updated_at = datetime.now()
            with self.cores_lock:
                self.active_cores.pop(task_id, None)

    def _render_job(self, core: VideoMatrixCore, idx: int, status: TaskStatus) -> bool:
        if status.status == "stopped" or not core.is_running:
            return False
        result, output_path, elapsed = core.render_single_video(idx, return_result=True)
        if result and output_path and status.task_id in self.tasks:
            if output_path not in self.tasks[status.task_id].output_files:
                self.tasks[status.task_id].output_files.append(output_path)
            if elapsed is not None:
                self.tasks[status.task_id].output_elapsed[output_path] = elapsed
        return result

    def stop_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            return False
        self.tasks[task_id].status = "stopped"
        with self.cores_lock:
            cores = self.active_cores.get(task_id, [])
            for core in cores:
                core.stop()
        return True

    def get_task(self, task_id: str) -> Optional[TaskStatus]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskStatus]:
        return list(self.tasks.values())

    def get_logs(self, task_id: str) -> List[str]:
        with self._log_lock:
            return list(self.log_buffers.get(task_id, []))

    def clear_history(self):
        self.shared_cache.clear_history()

    def preflight(self, config: VideoConfig) -> dict:
        raw_config = self._normalize_config(config.model_dump())
        tasks = self._get_tasks_from_config(raw_config)
        if not tasks:
            return {"ok": False, "error": "找不到任何素材目录", "report": []}

        report = []
        total_capacity = 0
        for t in tasks:
            task_cfg = raw_config.copy()
            task_cfg['task_name'] = t['name']
            task_cfg['hook_dir'] = t['hook_dir']
            task_cfg['body_dirs'] = t['body_dirs']
            core = VideoMatrixCore(task_cfg, lambda _x: None, self.shared_cache)
            ok, msg = core.pre_flight_check()
            if ok:
                capacity = core.n_total if isinstance(core.n_total, int) else "无限"
                total_capacity += core.n_total if isinstance(core.n_total, int) else 9999
                report.append({"name": t['name'], "ok": True, "capacity": capacity, "message": f"可产出: {capacity} 个"})
            else:
                report.append({"name": t['name'], "ok": False, "capacity": 0, "message": msg})

        cap_text = '充足/无限' if total_capacity > 9000 else total_capacity
        return {"ok": any(item["ok"] for item in report), "capacity": cap_text, "report": report}

    def get_benchmark(self, config: VideoConfig) -> dict:
        raw_config = self._normalize_config(config.model_dump())
        tasks = self._get_tasks_from_config(raw_config)
        if not tasks:
            return {"error": "素材不足以支撑压测"}

        results = {}
        log_cb = lambda x: None

        for n in range(1, 5):
            test_cfg = raw_config.copy()
            test_cfg.update({
                'hook_dir': tasks[0]['hook_dir'],
                'body_dirs': tasks[0]['body_dirs'],
                'out_dir': os.path.join(os.path.dirname(__file__), '../../../temp'),
                'target_count': 2,
                'task_name': 'Test'
            })
            os.makedirs(test_cfg['out_dir'], exist_ok=True)
            core = VideoMatrixCore(test_cfg, log_cb, self.shared_cache)
            if not core.pre_flight_check()[0]:
                return {"error": "素材不足以支撑压测"}

            start_t = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
                fs = [ex.submit(core.render_single_video, i) for i in range(1, n + 1)]
                concurrent.futures.wait(fs)
            elapsed = time.time() - start_t
            t_per_v = elapsed / n
            results[n] = {
                "concurrent": n,
                "total_time": round(elapsed, 2),
                "avg_per_video": round(t_per_v, 2)
            }

        best_n = min(results, key=lambda k: results[k]["avg_per_video"])
        return {
            "results": results,
            "best_concurrent": best_n,
            "best_result": results[best_n]
        }


# 全局单例
task_service = TaskService()
