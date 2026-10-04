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
import copy
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable

from ..core.video_matrix import VideoMatrixCore, SharedMediaCache
from ..core.video_variant import VideoVariantProcessor, derive_variant_seed
from ..core.timeline import apply_timeline_totals
from ..core.video_cover import RandomCoverProcessor
from ..core.hardware import HardwareSession, short_error
from ..core.output_settings import validate_output_settings
from ..models.schemas import VideoConfig, TaskStatus


class TaskBusyError(RuntimeError):
    """A requested operation conflicts with active production or benchmarking."""


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
        self._benchmark_task_id: Optional[str] = None

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
        validate_output_settings(normalized)
        if (normalized.get('random_resolution_enabled') or normalized.get('random_bitrate_enabled')) and normalized.get('_output_seed') is None:
            normalized['_output_seed'] = random.SystemRandom().randrange(0, 2**63)
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
        with self.cores_lock:
            if self._benchmark_task_id is not None:
                raise TaskBusyError("智能压测尚未结束，请等待压测或停止后的清理完成再启动生产。")
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
        with self.cores_lock:
            if status.status == 'stopped':
                return
            status.status = "running"
        status.updated_at = datetime.now()
        hardware_session = None
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
            if status.status == 'stopped':
                status.message = '已停止，未开始渲染。'
                return
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
                if status.status == 'stopped':
                    status.message = '已停止，未开始渲染。'
                    return
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
                self._finalize_status(status)
                log_cb(f">>> [{'完成' if status.status == 'completed' else '警告' if status.status == 'partial' else '失败'}] {status.message}")
                for core_name, cnt in success_counts.items():
                    target = next(c.config['target_count'] for c in cores if c.task_name == core_name)
                    log_cb(f"    [{core_name}] 可用视频: {cnt}/{target}（后处理警告见产出列表）")
            else:
                status.message = f"已停止，保留 {len(status.output_files)} 个可用视频；未完成项见产出说明。"
                log_cb(f">>> [停止] {status.message}")

        except Exception as e:
            if status.status == 'stopped':
                status.message = f'已停止，保留 {len(status.output_files)} 个可用视频。'
            else:
                log_cb(f"[严重错误] 调度引擎崩溃: {str(e)}")
                status.status = "failed"
                status.message = str(e)
        finally:
            if hardware_session is not None:
                hardware_session.close()
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

    @staticmethod
    def _finalize_status(status: TaskStatus):
        usable = len(status.output_files)
        warnings = sum(bool(status.output_warnings.get(file)) for file in status.output_files)
        failed = max(0, status.total - usable)
        status.status = "failed" if not usable else "partial" if warnings or failed else "completed"
        status.message = f"完整完成 {usable - warnings}/{status.total}，后处理未完成但已保留 {warnings} 个，失败 {failed} 个。"

    def _register_output(self, status: TaskStatus, path: str, elapsed, warnings):
        if not os.path.isfile(path):
            return False
        with self.cores_lock:
            if path not in status.output_files:
                status.output_files.append(path)
            if elapsed is not None:
                status.output_elapsed[path] = round(elapsed, 1)
            if warnings:
                status.output_warnings[path] = '；'.join(warnings)
            else:
                status.output_warnings.pop(path, None)
        return True

    def _render_job(self, core: VideoMatrixCore, idx: int, status: TaskStatus) -> bool:
        if status.status == "stopped" or not core.is_running:
            return False
        result, output_path, elapsed = core.render_single_video(idx, return_result=True)
        output_config = getattr(core, 'output_configs', {}).pop(idx, core.config)
        if not result or not output_path:
            return False
        stages = []
        if core.config.get('enable_variants') and not output_config.get('_variant_applied'):
            stages.append(('成品变换', self.variant_processor, derive_variant_seed(
                int(core.config.get('variant_seed') or 0), core.task_name, idx)))
        if core.config.get('enable_random_cover') and not output_config.get('_cover_applied'):
            stages.append(('随机封面', self.cover_processor, derive_variant_seed(
                int(core.config.get('_cover_seed') or 0), core.task_name + ':cover', idx)))
        warnings = list(output_config.get('_variant_warnings') or []) + list(output_config.get('_output_warnings') or [])
        for position, (stage, processor, seed) in enumerate(stages):
            if status.status == 'stopped' or not core.is_running:
                warnings.append('已停止，' + '、'.join(item[0] for item in stages[position:]) + '未完成')
                break
            core.log(f"    [{core.task_name}] 视频 {idx:03d} {stage}处理中…")
            stage_started = time.time()
            try:
                ok, error, summary = processor.process(
                    output_path, output_config, seed,
                    is_cancelled=lambda: status.status == "stopped" or not core.is_running,
                    on_process=lambda process: self._track_variant_process(status.task_id, process),
                )
            except Exception as exc:
                ok, error, summary = False, str(exc), None
            elapsed = (elapsed or 0) + time.time() - stage_started
            if ok:
                if stage == '随机封面' and summary:
                    core.log(f"    [{core.task_name}] 随机封面完成（{summary['mode']}）：取样约 {summary['sample_time']:.3f} 秒，zoom={summary['zoom']:.3f}")
                else:
                    core.log(f"    [{core.task_name}] {stage}完成")
            else:
                warnings.append(f"{stage}未完成：{short_error(error or '处理失败')}")
                core.log(f"    [{core.task_name}] {stage}警告：{short_error(error or '处理失败')}，已保留原成片。")
        if not self._register_output(status, output_path, elapsed, warnings):
            return False
        if warnings:
            core.log(f"    [{core.task_name}] 视频 {idx:03d} 已保留，后处理未全部完成，详情见产出列表。")
        else:
            core.log(f"    [{core.task_name}] 视频 {idx:03d} 最终完成，总耗时 {(elapsed or 0):.1f} 秒 -> {os.path.basename(output_path)}")
        return True

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
        with self.cores_lock:
            if self.tasks[task_id].status not in ('pending', 'running'):
                return False
            self.tasks[task_id].status = "stopped"
            cores = list(self.active_cores.get(task_id, []))
            processes = list(self.variant_processes.get(task_id, set()))
        for core in cores:
            core.stop()
        for process in processes:
            try:
                process.terminate()
            except Exception:
                pass
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

    @staticmethod
    def _benchmark_cache(directory, snapshot):
        cache = SharedMediaCache(str(directory))
        cache.media_cache = copy.deepcopy(snapshot[0])
        cache.usage_history = set(snapshot[1])
        return cache

    def _benchmark_release_cores(self, task_id, cores):
        identities = {id(core) for core in cores}
        with self.cores_lock:
            self.active_cores[task_id] = [
                core for core in self.active_cores.get(task_id, []) if id(core) not in identities
            ]
        for core in cores:
            temporary = getattr(core, 'temp_dir', None)
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OSError as exc:
                    self._create_log_callback(task_id)(f'>>> [压测] 临时文件暂未能清理：{exc}')

    def _benchmark_trial(self, config, plans, directory, snapshot, concurrency, visible, label, log):
        """Measure the same independent job plans through the production renderer."""
        directory.mkdir(parents=True, exist_ok=True)
        cache = self._benchmark_cache(directory / 'state', snapshot)
        trial = TaskStatus(task_id=visible.task_id, task_name=label, status='running',
                           created_at=datetime.now(), total=len(plans))
        # Only this private status receives output paths. The visible task tracks
        # cancellation/processes but must never advertise temporary test videos.
        reasons, encoders, limits = [], set(), []
        metrics_lock = threading.Lock()

        def update(values):
            self._update_acceleration(visible, values)
            with metrics_lock:
                if values.get('acceleration_warning'):
                    reasons.append(str(values['acceleration_warning']))
                if values.get('effective_concurrency'):
                    limits.append(int(values['effective_concurrency']))

        def record(message):
            log(message)
            with metrics_lock:
                if '[加速降级]' in message or '[加速恢复]' in message:
                    reasons.append(message)
                encoders.update(re.findall(r'\((h264_[a-z0-9_]+|libx264)\)', message))

        session, cores = None, []
        started = None
        visible.message = f'{label}：固定 {len(plans)} 条样本，{concurrency} 路并发'
        log(f'>>> [压测] {visible.message}')
        try:
            session = HardwareSession(
                {**config, 'concurrent_tasks': concurrency}, log=record, update=update,
                cancelled=lambda: visible.status == 'stopped',
            )
            for plan in plans:
                if visible.status == 'stopped':
                    break
                cfg = copy.deepcopy(plan['config'])
                output = directory / f"job-{plan['slot']:02d}"
                output.mkdir(exist_ok=True)
                cfg.update(out_dir=str(output), target_count=1, concurrent_tasks=concurrency,
                           _selection_seed=plan['seed'], _hardware_session=session)
                core = VideoMatrixCore(cfg, record, cache)
                core.hook_pool = [copy.deepcopy(plan['hook'])]
                for name, pool in plan['pools'].items():
                    # Production only reads these candidate pools while rendering;
                    # audio fitting copies its selected clips before truncation.
                    # Share one frozen snapshot instead of multiplying its memory
                    # by the number of jobs, which would distort high-load tests.
                    setattr(core, name, pool)
                cores.append(core)
                with self.cores_lock:
                    if visible.status == 'stopped':
                        core.stop()
                    else:
                        self.active_cores.setdefault(visible.task_id, []).append(core)

            def render(core, plan):
                if visible.status == 'stopped':
                    return False
                return self._render_job(core, plan['index'], trial)

            started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(render, core, plan) for core, plan in zip(cores, plans)]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        reasons.append(f'渲染异常：{exc}')
                        log(f'>>> [压测] {label}渲染异常：{exc}')
                    if visible.status != 'stopped':
                        visible.current += 1
                        visible.progress = min(99, int(visible.current / max(1, visible.total) * 100))
                        visible.updated_at = datetime.now()
                        log(f'>>> [压测进度] {visible.current}/{visible.total}（{visible.progress}%）')
            seconds = time.perf_counter() - started
        finally:
            # Executor.__exit__ joins workers before sessions/directories close.
            if session is not None:
                session.close()
            self._benchmark_release_cores(visible.task_id, cores)

        complete = [path for path in trial.output_files if not trial.output_warnings.get(path)]
        if len(complete) != len(plans):
            reasons.append(f'完整完成 {len(complete)}/{len(plans)} 条（后处理警告也视为未完整完成）')
        effective = min(limits, default=concurrency)
        if effective < concurrency:
            reasons.append(f'实际有效并发降至 {effective} 路，低于请求的 {concurrency} 路')
        if visible.status == 'stopped':
            reasons.append('用户已停止压测')
        reasons = list(dict.fromkeys(reasons))
        result = {
            'concurrent': concurrency, 'sample_count': len(plans),
            'completed_count': len(complete), 'usable_count': len(trial.output_files),
            'total_time': round(seconds, 3),
            'avg_per_video': round(seconds / len(complete), 3) if complete else None,
            'videos_per_minute': round(len(complete) * 60 / max(seconds, 0.001), 2),
            'avg_video_elapsed': round(sum(trial.output_elapsed.get(path, 0) for path in complete) / len(complete), 3) if complete else None,
            'stable': not reasons, 'reason': '；'.join(reasons), 'reasons': reasons,
            'effective_concurrency': effective, 'encoders': sorted(encoders),
        }
        log(f">>> [压测] {label}：完整 {len(complete)}/{len(plans)} 条，共 {seconds:.2f} 秒，"
            f"{result['videos_per_minute']:.2f} 条/分钟" + ('；不参与推荐：' + result['reason'] if reasons else '；稳定完成'))
        return result

    def get_benchmark(self, config: VideoConfig) -> dict:
        raw_config = self._normalize_config(config.model_dump())
        task_id = 'benchmark-' + str(uuid.uuid4())
        visible = TaskStatus(task_id=task_id, task_name='智能压测', status='running', created_at=datetime.now())
        with self.cores_lock:
            if (self._benchmark_task_id is not None
                    or any(task.status in ('pending', 'running') for task in self.tasks.values())
                    or any(self.active_cores.values()) or any(self.variant_processes.values())):
                raise TaskBusyError('已有生产任务或压测正在运行/停止清理，请等待结束后再压测。')
            self._benchmark_task_id = task_id
            self.tasks[task_id] = visible
            self.log_buffers[task_id] = []
        log = self._create_log_callback(task_id)
        root, output_base, templates = None, None, []
        response = {'task_id': task_id, 'sample_count': 0, 'note': '', 'results': {},
                    'verification_results': {}, 'best_concurrent': None, 'best_result': None}
        try:
            visible.message = '正在预检素材并冻结相同的压测样本'
            log('>>> [压测] 正在准备固定素材；各档使用相同样本、输出盘和完整后处理。')
            tasks = self._get_tasks_from_config(raw_config)
            output_base = Path(raw_config['base_out_dir']).resolve()
            output_base.mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix='.videomatrix-benchmark-', dir=str(output_base))).resolve()
            with self.shared_cache.lock:
                snapshot = (copy.deepcopy(self.shared_cache.media_cache), set(self.shared_cache.usage_history))
            prepared_cache = self._benchmark_cache(root / 'prepared-state', snapshot)
            master_seed = random.SystemRandom().randrange(0, 2**63)
            pool_names = ('body_pool', 'body_group_pools', 'bgm_pool', 'bgm_track_pools', 'voice_pool')
            for task in tasks:
                if visible.status == 'stopped':
                    break
                cfg = copy.deepcopy(raw_config)
                cfg.update(task_name=task['name'], hook_dir=task['hook_dir'], body_dirs=task['body_dirs'],
                           out_dir=str(root / 'prepare'), target_count=8,
                           _selection_seed=derive_variant_seed(master_seed, task['name'], 0))
                core = VideoMatrixCore(cfg, log, prepared_cache)
                templates.append(core)
                with self.cores_lock:
                    if visible.status == 'stopped':
                        core.stop()
                    else:
                        self.active_cores.setdefault(task_id, []).append(core)
                ok, message = core.pre_flight_check()
                if not ok:
                    log(f'>>> [压测] {task["name"]}预检未通过：{message}')
                    core.hook_pool = []
                elif cfg.get('enable_srt'):
                    cfg['_benchmark_srt_files'] = sorted(core._scan_files(cfg['srt_dir'], ('.srt',)))

            # Select Hooks once in the same round-robin order as production.
            # Each job gets its own RNG, so thread scheduling cannot change its
            # Body/BGM/voice/subtitle choices when concurrency changes.
            plans, indices, cycles, frozen_pools = [], {}, {}, {}
            while len(plans) < 8 and visible.status != 'stopped':
                added = False
                for template in templates:
                    if len(plans) >= 8:
                        break
                    if not template.hook_pool:
                        continue
                    key = id(template)
                    cfg = template.config
                    if key not in frozen_pools:
                        frozen_pools[key] = {name: copy.deepcopy(getattr(template, name)) for name in pool_names}
                    if cfg.get('hook_full_duration'):
                        if not cycles.get(key):
                            cycles[key] = list(template.hook_pool)
                            template.rng.shuffle(cycles[key])
                        hook = cycles[key].pop()
                    elif cfg['hook_r'] >= 0.99:
                        hook = template.rng.choice(template.hook_pool)
                    else:
                        hook = template.hook_pool.pop()
                    index = indices.get(key, 0) + 1
                    indices[key] = index
                    plans.append({
                        'slot': len(plans) + 1, 'index': index,
                        'config': copy.deepcopy(cfg), 'hook': copy.deepcopy(hook),
                        'seed': derive_variant_seed(master_seed, template.task_name, index),
                        'pools': frozen_pools[key],
                    })
                    added = True
                if not added:
                    break
            self._benchmark_release_cores(task_id, templates)
            templates.clear()
            sample_count = len(plans)
            response['sample_count'] = sample_count
            if visible.status == 'stopped':
                response['cancelled'] = True
                response['error'] = '智能压测已停止。'
                return response
            if not plans:
                visible.status, visible.message = 'failed', '没有可用素材支撑压测。'
                response['error'] = visible.message
                return response
            covered = len({plan['config']['task_name'] for plan in plans})
            response['note'] = f'固定 {sample_count} 条样本，覆盖 {covered} 个素材库；预热不计时，以复测整批完成时间选优。'
            if sample_count < 8:
                response['note'] += '剩余可用 Hook 不足 8 条，样本不足，不自动推荐并发。'
            log('>>> [压测] ' + response['note'])
            visible.total = 1 + sample_count * 6
            with prepared_cache.lock:
                trial_snapshot = (copy.deepcopy(prepared_cache.media_cache), set(snapshot[1]))

            def trial(n, label, subset=plans):
                return self._benchmark_trial(raw_config, subset, root / label, trial_snapshot, n, visible, label, log)

            trial(1, '预热', plans[:1])
            for n in range(1, 5):
                if visible.status == 'stopped':
                    break
                response['results'][n] = trial(n, f'初测-{n}路')
            if visible.status != 'stopped':
                stable = [n for n, result in response['results'].items() if result['stable']]
                finalists = sorted(stable, key=lambda n: response['results'][n]['total_time'])[:2]
                visible.total = 1 + sample_count * (4 + len(finalists))
                # Reverse the original order to reduce consistent warm/cache bias.
                for n in sorted(finalists, reverse=True):
                    if visible.status == 'stopped':
                        break
                    response['verification_results'][n] = trial(n, f'复测-{n}路')
            if visible.status == 'stopped':
                response['cancelled'] = True
                response['error'] = '智能压测已停止，未改变并发设置。'
                visible.message = '智能压测已停止，等待临时样片清理完成。'
                return response
            verified = {n: result for n, result in response['verification_results'].items() if result['stable']}
            if verified and sample_count >= 8:
                fastest = min(result['total_time'] for result in verified.values())
                best = min(n for n, result in verified.items() if result['total_time'] <= fastest * 1.05)
                response['best_concurrent'], response['best_result'] = best, verified[best]
                visible.status = 'completed'
                visible.message = f'压测完成，复测推荐 {best} 路；5% 以内的差距优先较低并发。'
            else:
                visible.status = 'partial'
                visible.message = '压测完成，但样本不足或没有稳定的完整复测结果，不自动调整并发。'
                response['note'] += ' 没有足够可信的结果，保留当前并发设置。'
            visible.progress = 100
            log('>>> [压测完成] ' + visible.message)
            return response
        except Exception as exc:
            if visible.status != 'stopped':
                visible.status, visible.message = 'failed', f'智能压测失败：{exc}'
            log('>>> [压测] ' + visible.message)
            raise
        finally:
            try:
                self._benchmark_release_cores(task_id, templates)
                if root is not None and root.exists():
                    if root.resolve() != root or root.parent != output_base or not root.name.startswith('.videomatrix-benchmark-'):
                        log(f'>>> [压测] 临时路径校验失败，已保留目录：{root}')
                    else:
                        shutil.rmtree(root)
            except OSError as exc:
                log(f'>>> [压测] 临时目录未能清理：{root}（{exc}）')
            finally:
                visible.updated_at = datetime.now()
                with self.cores_lock:
                    self.active_cores.pop(task_id, None)
                    self.variant_processes.pop(task_id, None)
                    self._benchmark_task_id = None


# 全局单例
task_service = TaskService()
