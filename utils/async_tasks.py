#!/usr/bin/env python3
"""
异步任务处理系统
简化版实现，用于处理能力趋势分析等耗时任务

升级建议：
- 生产环境建议使用 Celery + Redis/RabbitMQ
- 当前实现适合中小规模应用
"""
import threading
import queue
import time
import json
import traceback
import uuid
from datetime import datetime
import logging

# 配置日志
logger = logging.getLogger(__name__)

class AsyncTaskManager:
    """异步任务管理器"""
    
    def __init__(self):
        self.task_queue = queue.Queue(maxsize=1000)
        self.workers = []
        self.is_running = False
        self.app = None  # 保存Flask应用实例
        self.logger = None  # 保存logger引用
        self._state_lock = threading.RLock()
        self._dedupe_tasks = {}
        self._max_queue_size = 1000
        self._retry_delay = 5.0
        
    def start(self, worker_count=2, app=None):
        """启动异步任务处理器"""
        if app is not None:
            self.app = app
            self.logger = app.logger
            self._max_queue_size = int(app.config.get('ASYNC_MAX_QUEUE_SIZE', 1000))
            self._retry_delay = float(app.config.get('ASYNC_RETRY_DELAY_SECONDS', 5))
            if self.task_queue.maxsize != self._max_queue_size and not self.is_running:
                self.task_queue = queue.Queue(maxsize=self._max_queue_size)
        if self.is_running:
            return
            
        self.logger = app.logger if app else logger  # 使用Flask应用的logger
        worker_count = int(worker_count if worker_count is not None else 1)
        worker_count = max(0, min(worker_count, 8))
        if worker_count == 0:
            self.logger.info("异步任务处理器已禁用（worker_count=0）")
            return
        self.is_running = True
        self.logger.info(f"启动异步任务处理器，工作线程数: {worker_count}")
        
        for i in range(worker_count):
            worker = threading.Thread(
                target=self._worker, 
                args=(f"worker-{i+1}",),
                daemon=True
            )
            worker.start()
            self.workers.append(worker)
            
    def stop(self):
        """停止任务处理器"""
        self.is_running = False
        if self.logger:
            self.logger.info("异步任务处理器已停止")
        
    def add_task(self, task_type, **kwargs):
        """添加任务到队列"""
        dedupe_key = kwargs.pop('_dedupe_key', None)
        task = {
            'id': f"{task_type}_{uuid.uuid4().hex}",
            'type': task_type,
            'data': kwargs,
            'created_at': datetime.utcnow(),
            'attempts': 0,
            'dedupe_key': dedupe_key,
        }

        # 检查、入队和登记去重键必须在同一个锁内完成。否则 worker 可能
        # 在登记前就处理完任务，或两个并发请求同时绕过去重检查。
        with self._state_lock:
            if dedupe_key and dedupe_key in self._dedupe_tasks:
                return self._dedupe_tasks[dedupe_key]
            try:
                self.task_queue.put_nowait(task)
            except queue.Full:
                if self.logger:
                    self.logger.warning("异步任务队列已满，丢弃任务: %s", task['id'])
                return None
            if dedupe_key:
                self._dedupe_tasks[dedupe_key] = task['id']
        if self.logger:
            self.logger.info(f"任务已添加到队列: {task['id']} (类型: {task_type})")
        return task['id']

    def _release_dedupe_key(self, task):
        dedupe_key = task.get('dedupe_key')
        if not dedupe_key:
            return
        with self._state_lock:
            if self._dedupe_tasks.get(dedupe_key) == task.get('id'):
                self._dedupe_tasks.pop(dedupe_key, None)
        
    def _worker(self, worker_name):
        """工作线程主循环"""
        if self.logger:
            self.logger.info(f"工作线程 {worker_name} 已启动")
        
        while self.is_running:
            try:
                # 从队列获取任务，超时1秒
                task = self.task_queue.get(timeout=1)
                try:
                    self._process_task(task, worker_name)
                finally:
                    self.task_queue.task_done()
                
            except queue.Empty:
                # 队列为空，继续循环
                continue
            except Exception as e:
                if self.logger:
                    self.logger.error(f"工作线程 {worker_name} 处理任务时出错: {e}")
                    self.logger.error(traceback.format_exc())
                
        if self.logger:
            self.logger.info(f"工作线程 {worker_name} 已停止")
        
    def _process_task(self, task, worker_name):
        """处理单个任务"""
        task_id = task['id']
        task_type = task['type']
        
        if self.logger:
            self.logger.info(f"{worker_name} 开始处理任务: {task_id}")
        start_time = time.time()
        
        try:
            if task_type == 'update_ability_trend':
                self._handle_ability_trend_update(task)
            elif task_type == 'batch_update_trends':
                self._handle_batch_update(task)
            elif task_type == 'generate_thinking_preset':
                self._handle_generate_preset(task)
            else:
                if self.logger:
                    self.logger.warning(f"未知任务类型: {task_type}")
                self._release_dedupe_key(task)
                return
                
            execution_time = time.time() - start_time
            if self.logger:
                self.logger.info(f"任务完成: {task_id} (耗时: {execution_time:.2f}s)")
            self._release_dedupe_key(task)
            
        except Exception as e:
            execution_time = time.time() - start_time
            task['attempts'] += 1
            
            if self.logger:
                self.logger.error(f"任务失败: {task_id} (耗时: {execution_time:.2f}s, 尝试次数: {task['attempts']})")
                self.logger.error(f"错误详情: {e}")
                self.logger.error(traceback.format_exc())
            
            # 重试机制（最多3次）
            if task['attempts'] < 3:
                if self.logger:
                    self.logger.info(f"任务 {task_id} 将在%.1f秒后重试", self._retry_delay)
                retry_timer = threading.Timer(
                    self._retry_delay,
                    self._requeue_task,
                    args=(task,),
                )
                retry_timer.daemon = True
                retry_timer.start()
            else:
                if self.logger:
                    self.logger.error(f"任务 {task_id} 重试次数已达上限，标记为失败")
                self._mark_task_failed(task)
                self._release_dedupe_key(task)

    def _requeue_task(self, task):
        if not self.is_running:
            self._release_dedupe_key(task)
            return
        try:
            self.task_queue.put_nowait(task)
        except queue.Full:
            if self.logger:
                self.logger.warning("重试时任务队列已满，任务标记失败: %s", task.get('id'))
            self._mark_task_failed(task)
            self._release_dedupe_key(task)
                
    def _handle_ability_trend_update(self, task):
        """处理能力趋势更新任务"""
        from models import db, AbilityTrend, Submission, User, Assignment
        from services.ai_evaluator import AIEvaluator
        from utils.access import assignment_target_class_filter, authoritative_class_name
        import os
        
        data = task['data']
        student_id = data['student_id']
        
        if self.logger:
            self.logger.info(f"开始分析学生 {student_id} 的能力发展趋势")
        
        # 在应用上下文中执行
        if not self.app:
            if self.logger:
                self.logger.error(f"Flask应用实例未设置，无法处理学生 {student_id} 的任务")
            return
            
        with self.app.app_context():
            try:
                # 标记为处理中
                trend = AbilityTrend.get_or_create(student_id)
                trend.status = 'processing'
                db.session.commit()
                
                # 获取学生信息和已分配的作业
                user = User.query.get(student_id)
                if not user:
                    self.logger.warning(f"找不到学生 {student_id}，无法进行能力分析")
                    trend.status = 'failed'
                    db.session.commit()
                    return

                class_name = authoritative_class_name(user)
                assigned_assignment_ids = []
                if class_name:
                    assigned_assignments = Assignment.query.filter(
                        assignment_target_class_filter(class_name)
                    ).all()
                    assigned_assignment_ids = [a.id for a in assigned_assignments]

                if not assigned_assignment_ids:
                    self.logger.info(f"学生 {student_id} 所在的班级没有分配任何作业，跳过分析")
                    trend.status = 'completed' # No work to do, so it's "complete"
                    trend.trend_data = json.dumps({
                        "trend": "暂无已分配的作业",
                        "improvement": "请等待教师分配作业后再进行分析。",
                        "suggestions": []
                    })
                    db.session.commit()
                    return

                # 获取学生对已分配作业的提交记录
                submissions = Submission.query.filter(
                    Submission.student_id == student_id,
                    Submission.assignment_id.in_(assigned_assignment_ids)
                ).all()
                
                submission_data = []
                for sub in submissions:
                    if sub.code and sub.assignment:
                        submission_data.append({
                            'assignment_title': sub.assignment.title,
                            'code': sub.code,
                            'score': sub.score,
                            'submitted_at': sub.submitted_at.strftime('%Y-%m-%d %H:%M:%S')
                        })
                
                if not submission_data:
                    if self.logger:
                        self.logger.info(f"学生 {student_id} 对已分配的作业暂无提交记录，跳过分析")
                    trend.status = 'completed'
                    trend.trend_data = json.dumps({
                        "trend": "您还没有对已分配的作业进行任何提交",
                        "improvement": "请先完成一些作业再来分析能力趋势。",
                        "suggestions": []
                    })
                    db.session.commit()
                    return
                
                # 初始化共享 AI 客户端；它会负责 provider 选择、重试和熔断。
                from services.llm_client import SharedLLMClient
                if not SharedLLMClient().is_available():
                    if self.logger:
                        self.logger.warning(f"API密钥未设置，无法为学生 {student_id} 进行AI分析")
                    trend.status = 'failed'
                    db.session.commit()
                    return
                    
                ai_evaluator = AIEvaluator()
                
                # 执行AI分析
                if self.logger:
                    self.logger.info(f"调用AI API分析学生 {student_id} 的 {len(submission_data)} 次提交")
                ability_analysis = ai_evaluator.analyze_ability_trend(submission_data)

                # 检查AI分析是否返回错误
                if isinstance(ability_analysis, dict) and ability_analysis.get("trend") == "分析过程中出现错误":
                    self.logger.error(f"AI分析为学生 {student_id} 返回了一个错误，标记任务为失败")
                    trend.status = 'failed'
                    db.session.commit()
                    return

                # 保存结果
                AbilityTrend.update_trend(student_id, ability_analysis, len(submission_data))
                if self.logger:
                    self.logger.info(f"学生 {student_id} 的能力趋势分析完成并已保存")
                
            except Exception as e:
                if self.logger:
                    self.logger.error(f"处理学生 {student_id} 的能力趋势分析失败: {e}")
                if self.app:
                    with self.app.app_context():
                        from models import db, AbilityTrend
                        trend = AbilityTrend.get_or_create(student_id)
                        trend.status = 'failed'
                        db.session.commit()
                else:
                    if self.logger:
                        self.logger.error("Flask应用实例未设置，无法标记任务失败状态")
                raise
                
    def _handle_generate_preset(self, task):
        """处理生成引导式学习预设任务"""
        from models import db, Assignment, AssignmentThinkingPreset
        from utils.thinking_ai import generate_preset
        import json
        
        data = task['data']
        assignment_id = data['assignment_id']
        
        if self.logger:
            self.logger.info(f"开始为作业 {assignment_id} 生成引导式学习预设")
            
        if not self.app:
            if self.logger:
                self.logger.error(f"Flask应用实例未设置，无法处理作业 {assignment_id} 的预设生成任务")
            return
            
        with self.app.app_context():
            try:
                assignment = Assignment.query.get(assignment_id)
                if not assignment:
                    self.logger.error(f"找不到作业 {assignment_id}，无法生成预设")
                    return
                    
                preset = AssignmentThinkingPreset.query.filter_by(assignment_id=assignment_id).first()
                if preset and preset.status == 'ready':
                    if self.logger:
                        self.logger.info(f"作业 {assignment_id} 的预设已就绪，跳过后台生成")
                    return
                if not preset:
                    preset = AssignmentThinkingPreset(assignment_id=assignment_id)
                    db.session.add(preset)
                    
                preset.status = 'generating'
                db.session.commit()
                
                result = generate_preset(
                    assignment.title,
                    assignment.description or ''
                )
                
                preset.reference_code = result.get('reference_code', '')
                preset.key_steps = json.dumps(result.get('key_steps', []), ensure_ascii=False)
                preset.code_blocks = json.dumps(result.get('code_blocks', []), ensure_ascii=False)
                preset.noise_blocks = json.dumps(result.get('noise_blocks', []), ensure_ascii=False)
                preset.quiz_steps = json.dumps(result.get('quiz_steps', []), ensure_ascii=False)
                preset.difficulty_config = json.dumps(result.get('difficulty_config', {}), ensure_ascii=False)
                preset.algorithm_summary = result.get('algorithm_summary', '')
                preset.status = 'ready'
                preset.error_message = None
                
                db.session.commit()
                
                if self.logger:
                    self.logger.info(f"作业 {assignment_id} 的引导式学习预设生成完成")
                    
            except Exception as e:
                if self.logger:
                    self.logger.error(f"处理作业 {assignment_id} 的预设生成失败: {e}")
                preset = AssignmentThinkingPreset.query.filter_by(assignment_id=assignment_id).first()
                if preset:
                    preset.status = 'failed'
                    preset.error_message = str(e)
                    db.session.commit()
                raise

    def _handle_batch_update(self, task):
        """处理批量更新任务"""
        data = task['data']
        student_ids = data.get('student_ids', [])
        
        if self.logger:
            self.logger.info(f"开始批量更新 {len(student_ids)} 个学生的能力趋势")
        
        for student_id in student_ids:
            # 为每个学生创建单独的任务
            self.add_task('update_ability_trend', student_id=student_id)
            
    def _mark_task_failed(self, task):
        """标记任务失败"""
        if task['type'] == 'update_ability_trend':
            student_id = task['data']['student_id']
            try:
                if self.app:
                    with self.app.app_context():
                        from models import db, AbilityTrend
                        trend = AbilityTrend.get_or_create(student_id)
                        trend.status = 'failed'
                        db.session.commit()
                else:
                    if self.logger:
                        self.logger.error("Flask应用实例未设置，无法标记任务失败状态")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"标记任务失败状态时出错: {e}")
        elif task['type'] == 'generate_thinking_preset':
            assignment_id = task['data']['assignment_id']
            try:
                if self.app:
                    with self.app.app_context():
                        from models import db, AssignmentThinkingPreset
                        preset = AssignmentThinkingPreset.query.filter_by(assignment_id=assignment_id).first()
                        if preset:
                            preset.status = 'failed'
                            preset.error_message = "多次尝试生成失败"
                            db.session.commit()
                else:
                    if self.logger:
                        self.logger.error("Flask应用实例未设置，无法标记任务失败状态")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"标记任务失败状态时出错: {e}")

# 全局任务管理器实例
task_manager = AsyncTaskManager()

def init_async_tasks(app):
    """初始化异步任务系统"""
    if app.extensions.get('codesense_async_tasks_initialized'):
        return
    app.extensions['codesense_async_tasks_initialized'] = True

    with app.app_context():
        task_manager.start(
            worker_count=app.config.get('ASYNC_WORKER_COUNT', 1),
            app=app,
        )
        app.logger.info(
            "异步任务系统已启动（worker_count=%s）",
            app.config.get('ASYNC_WORKER_COUNT', 1),
        )

        # 启动后台线程异步扫描缺失预设的作业，并在后台自动生成
        def scan_and_trigger_presets():
            time.sleep(5)  # 延迟5秒启动扫描，避免影响主服务器启动
            with app.app_context():
                try:
                    from models import db, Assignment, AssignmentThinkingPreset
                    from sqlalchemy import or_
                    
                    # 重置所有因服务器重启而中断的 'generating' 悬空预设为 'failed'
                    stuck_presets = AssignmentThinkingPreset.query.filter_by(status='generating').all()
                    if stuck_presets:
                        app.logger.info(f"[后台预生成] 检测到 {len(stuck_presets)} 个悬空预设任务，重置为 'failed' 以便重新扫描...")
                        for p in stuck_presets:
                            p.status = 'failed'
                            p.error_message = "服务器重启导致后台生成任务中断"
                        db.session.commit()
                    
                    # 查找缺失或未就绪预设的作业
                    missing_presets = db.session.query(Assignment).outerjoin(
                        AssignmentThinkingPreset, Assignment.id == AssignmentThinkingPreset.assignment_id
                    ).filter(
                        or_(
                            AssignmentThinkingPreset.id == None,
                            ~AssignmentThinkingPreset.status.in_(['ready', 'generating'])
                        )
                    ).limit(app.config.get('PRESET_SCAN_BATCH_SIZE', 20)).all()
                    
                    triggered_count = 0
                    for a in missing_presets:
                        preset = AssignmentThinkingPreset.query.filter_by(assignment_id=a.id).first()
                        if not preset:
                            preset = AssignmentThinkingPreset(assignment_id=a.id, status='generating')
                            db.session.add(preset)
                        else:
                            preset.status = 'generating'
                        task_id = add_generate_preset_task(a.id)
                        if task_id is None:
                            # 队列满时不要留下永远不会被 worker 处理的
                            # generating 状态，下一轮扫描可以安全地重试。
                            preset.status = 'failed'
                            preset.error_message = '后台任务队列已满，请稍后重试'
                            continue
                        triggered_count += 1

                    # 统一提交状态更新；AI 客户端本身有并发上限、重试和熔断，
                    # 不再让扫描线程为每个作业阻塞 15 秒。
                    if triggered_count:
                        db.session.commit()
                        
                    if triggered_count > 0:
                        app.logger.info(f"[后台预生成] 检测到 {triggered_count} 个作业缺少引导式学习数据，已自动加入生成队列")
                except Exception as e:
                    app.logger.error(f"[后台预生成] 自动扫描作业预设失败: {e}")

        if app.config.get('PRESET_SCAN_ENABLED', True):
            thread = threading.Thread(target=scan_and_trigger_presets, daemon=True)
            thread.start()

def add_ability_trend_task(student_id):
    """添加能力趋势分析任务"""
    return task_manager.add_task(
        'update_ability_trend',
        student_id=student_id,
        _dedupe_key=f'ability:{student_id}',
    )

def add_batch_trend_update(student_ids):
    """添加批量趋势更新任务"""
    return task_manager.add_task('batch_update_trends', student_ids=student_ids)

def add_generate_preset_task(assignment_id):
    """添加生成引导式学习预设任务"""
    return task_manager.add_task(
        'generate_thinking_preset',
        assignment_id=assignment_id,
        _dedupe_key=f'preset:{assignment_id}',
    )
