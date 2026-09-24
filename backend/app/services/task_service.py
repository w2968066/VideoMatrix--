import os
import json
import time
import uuid
import threading
import concurrent.futures
import random
import subprocess
import shutil
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Callable

from ..core.video_matrix import VideoMatrixCore, SharedMediaCache
from ..core.video_variant import VideoVariantProcessor, derive_variant_seed
from ..core.timeline import apply_timeline_totals
from ..core.video_cover import RandomCoverProcessor
from ..core.hardware import HardwareSession
from ..models.schemas import VideoConfig, TaskStatus


class TaskService:
    def __init__(self, shared_cache: Optional[SharedMediaCache] = None):
        self.shared_cache = shared_cache or SharedMediaCache()
        self.tasks: Dict[str, TaskStatus] = {}
        self.active_cores: Dict[str, List[VideoMatrixCore]] = {}
        self.cores_lock = threading.Lock()
        self.log_buffers: Dict[str, List[str]] = {}
        self._log_lock = threading.Lock()
        self.variant_processor = VideoVariantProcessor()
        self.cover_processor = RandomCoverProcessor()
        self.variant_processes: Dict[str, set[subprocess.Popen]] = {}

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

        if normalized.get('body_mode', 'normal') == 'grouped':
            normalized['body_groups'] = list(normalized.get('body_groups') or [])
        apply_timeline_totals(normalized)

        if normalized.get('enable_variants') and normalized.get('variant_seed') is None:
            normalized['variant_seed'] = random.SystemRandom().randrange(0, 2**63)
        if normalized.get('enable_random_cover'):
            # Cover randomness is intentionally independent from optional variant settings.
            normalized['_cover_seed'] = random.SystemRandom().randrange(0, 2**63)

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
            # One verified hardware session is shared by all SKU cores and all
            # post-processing stages. This prevents each stage from probing or
            # oversubscribing the same encoder independently.
            hardware_session = HardwareSession(
                config,
                log=log_cb,
                update=lambda values: self._update_acceleration(status, values),
                cancelled=lambda: status.status == "stopped",
            )
            config['_hardware_session'] = hardware_session
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
                # Group folders are global configuration, not inferred from Hook subfolders.
                task_cfg['body_groups'] = config.get('body_groups') or []
                task_cfg['out_dir'] = t['out']
                task_cfg['_hardware_session'] = hardware_session
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

            concurrent_limit = max(1, int(config.get('concurrent_tasks', 3)))
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
                self.variant_processes.pop(task_id, None)

    @staticmethod
    def _update_acceleration(status: TaskStatus, values: dict):
        """Expose the effective backend without making users run a report."""
        if "acceleration" in values:
            status.acceleration = str(values["acceleration"] or "")
        if "acceleration_warning" in values:
            status.acceleration_warning = str(values["acceleration_warning"] or "")
        if "effective_concurrency" in values:
            status.effective_concurrency = max(0, int(values["effective_concurrency"]))

    def _render_job(self, core: VideoMatrixCore, idx: int, status: TaskStatus) -> bool:
        if status.status == "stopped" or not core.is_running:
            return False
        result, output_path, elapsed = core.render_single_video(idx, return_result=True)
        output_config = getattr(core, 'output_configs', {}).pop(idx, core.config)
        if result and output_path and core.config.get('enable_variants') and not output_config.get('_variant_applied'):
            variant_started = time.time()
            seed = derive_variant_seed(
                int(core.config.get('variant_seed') or 0), core.task_name, idx,
            )
            try:
                ok, error, summary = self.variant_processor.process(
                    output_path,
                    output_config,
                    seed,
                    is_cancelled=lambda: status.status == "stopped" or not core.is_running,
                    on_process=lambda process: self._track_variant_process(status.task_id, process),
                )
            except Exception as exc:
                ok, error, summary = False, str(exc), None
            elapsed = round((elapsed or 0) + time.time() - variant_started, 1)
            if status.status == "stopped" or not core.is_running:
                return False
            if ok:
                core.log(
                    f"    [{core.task_name}] 成品变换完成：{summary['segments']} 段独立随机，"
                    f"镜像 {summary['mirrored']} 段，帧混合 {summary['frame_mixed']} 段，"
                    f"seed={summary['seed']}"
                )
            else:
                core.log(f"    [{core.task_name}] 成品变换警告：{error or '处理失败'}，已保留原成片。")
        if result and output_path and core.config.get('enable_random_cover'):
            cover_started = time.time()
            seed = derive_variant_seed(
                int(core.config.get('_cover_seed') or 0), core.task_name + ':cover', idx,
            )
            try:
                ok, error, summary = self.cover_processor.process(
                    output_path, output_config, seed,
                    is_cancelled=lambda: status.status == "stopped" or not core.is_running,
                    on_process=lambda process: self._track_variant_process(status.task_id, process),
                )
            except Exception as exc:
                ok, error, summary = False, str(exc), None
            elapsed = round((elapsed or 0) + time.time() - cover_started, 1)
            if status.status == "stopped" or not core.is_running:
                return False
            if ok:
                core.log(f"    [{core.task_name}] 随机封面完成（{summary['mode']}）：取样 {summary['sample_time']:.3f} 秒，zoom={summary['zoom']:.3f}")
            else:
                core.log(f"    [{core.task_name}] 随机封面警告：{error or '处理失败'}，已保留原成片。")
        if result and output_path and status.task_id in self.tasks:
            if output_path not in self.tasks[status.task_id].output_files:
                self.tasks[status.task_id].output_files.append(output_path)
            if elapsed is not None:
                self.tasks[status.task_id].output_elapsed[output_path] = elapsed
        return result

    def _track_variant_process(self, task_id: str, process: Optional[subprocess.Popen]):
        with self.cores_lock:
            processes = self.variant_processes.setdefault(task_id, set())
            if process is None:
                processes_copy = {item for item in processes if item.poll() is None}
                if processes_copy:
                    self.variant_processes[task_id] = processes_copy
                else:
                    self.variant_processes.pop(task_id, None)
            else:
                processes.add(process)

    def stop_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            return False
        self.tasks[task_id].status = "stopped"
        with self.cores_lock:
            cores = self.active_cores.get(task_id, [])
            for core in cores:
                core.stop()
            for process in list(self.variant_processes.get(task_id, set())):
                try:
                    process.terminate()
                except Exception:
                    pass
            self.variant_processes.pop(task_id, None)
        return True

    def stop_all_tasks(self) -> int:
        stopped = 0
        active_ids = [
            task_id for task_id, task in self.tasks.items()
            if task.status in ("pending", "running")
        ]
        for task_id in active_ids:
            if self.stop_task(task_id):
                stopped += 1
        return stopped

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
            task_cfg['body_groups'] = raw_config.get('body_groups') or []
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
            benchmark_dir = tempfile.mkdtemp(prefix="videomatrix-benchmark-")
            test_cfg.update({
                'hook_dir': tasks[0]['hook_dir'],
                'body_dirs': tasks[0]['body_dirs'],
                'body_groups': raw_config.get('body_groups') or [],
                'out_dir': benchmark_dir,
                'target_count': 2,
                'task_name': 'Test'
            })
            try:
                os.makedirs(test_cfg['out_dir'], exist_ok=True)
                # Never consume production usage history during a benchmark.
                core = VideoMatrixCore(test_cfg, log_cb, SharedMediaCache(benchmark_dir))
                if not core.pre_flight_check()[0]:
                    return {"error": "素材不足以支撑压测"}
                benchmark_status = TaskStatus(
                    task_id=f"benchmark-{n}", task_name="Benchmark", status="running",
                    created_at=datetime.now(), total=n,
                )

                start_t = time.time()
                with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
                    fs = [ex.submit(self._render_job, core, i, benchmark_status)
                          for i in range(1, n + 1)]
                    results_raw = [future.result() for future in fs]
                elapsed = time.time() - start_t
                successful = [item for item in results_raw if item]
                if len(successful) != n:
                    continue
                t_per_v = elapsed / n
                results[n] = {
                    "concurrent": n,
                    "total_time": round(elapsed, 2),
                    "avg_per_video": round(t_per_v, 2)
                }
            finally:
                shutil.rmtree(benchmark_dir, ignore_errors=True)

        if not results:
            return {"error": "压测期间没有成功完成可比较的成品"}
        best_n = min(results, key=lambda k: results[k]["avg_per_video"])
        return {
            "results": results,
            "best_concurrent": best_n,
            "best_result": results[best_n]
        }


# 全局单例
task_service = TaskService()
