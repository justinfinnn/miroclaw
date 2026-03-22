"""
Report API路由
提供模拟报告生成、获取、对话等接口
"""

import os
import traceback
import threading
from flask import request, jsonify, send_file

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.cancellation import OperationCanceledError
from ..services.simulation_manager import SimulationManager
from ..models.project import ProjectManager
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.report')

_report_cancel_lock = threading.Lock()
_report_cancel_events_by_task = {}
_report_cancel_events_by_report = {}
_report_cancel_events_by_simulation = {}


def _register_report_cancel_event(
    task_id: str,
    report_id: str,
    simulation_id: str,
    cancel_event: threading.Event,
) -> None:
    with _report_cancel_lock:
        _report_cancel_events_by_task[task_id] = {
            "report_id": report_id,
            "simulation_id": simulation_id,
            "event": cancel_event,
        }
        _report_cancel_events_by_report[report_id] = {
            "task_id": task_id,
            "simulation_id": simulation_id,
            "event": cancel_event,
        }
        _report_cancel_events_by_simulation[simulation_id] = {
            "task_id": task_id,
            "report_id": report_id,
            "event": cancel_event,
        }


def _get_report_cancel_entry(task_id: str = None, report_id: str = None, simulation_id: str = None):
    with _report_cancel_lock:
        if task_id and task_id in _report_cancel_events_by_task:
            entry = _report_cancel_events_by_task[task_id]
            return {
                "task_id": task_id,
                "report_id": entry["report_id"],
                "simulation_id": entry["simulation_id"],
                "event": entry["event"],
            }
        if report_id and report_id in _report_cancel_events_by_report:
            entry = _report_cancel_events_by_report[report_id]
            return {
                "task_id": entry["task_id"],
                "report_id": report_id,
                "simulation_id": entry["simulation_id"],
                "event": entry["event"],
            }
        if simulation_id and simulation_id in _report_cancel_events_by_simulation:
            entry = _report_cancel_events_by_simulation[simulation_id]
            return {
                "task_id": entry["task_id"],
                "report_id": entry["report_id"],
                "simulation_id": simulation_id,
                "event": entry["event"],
            }
    return None


def _clear_report_cancel_event(task_id: str = None, report_id: str = None, simulation_id: str = None) -> None:
    with _report_cancel_lock:
        resolved_task_id = task_id
        resolved_report_id = report_id
        resolved_simulation_id = simulation_id

        if resolved_task_id and not resolved_report_id:
            entry = _report_cancel_events_by_task.get(resolved_task_id)
            if entry:
                resolved_report_id = entry["report_id"]
                resolved_simulation_id = resolved_simulation_id or entry["simulation_id"]

        if resolved_report_id and not resolved_task_id:
            entry = _report_cancel_events_by_report.get(resolved_report_id)
            if entry:
                resolved_task_id = entry["task_id"]
                resolved_simulation_id = resolved_simulation_id or entry["simulation_id"]

        if resolved_simulation_id and (not resolved_task_id or not resolved_report_id):
            entry = _report_cancel_events_by_simulation.get(resolved_simulation_id)
            if entry:
                resolved_task_id = resolved_task_id or entry["task_id"]
                resolved_report_id = resolved_report_id or entry["report_id"]

        if resolved_task_id:
            _report_cancel_events_by_task.pop(resolved_task_id, None)
        if resolved_report_id:
            _report_cancel_events_by_report.pop(resolved_report_id, None)
        if resolved_simulation_id:
            _report_cancel_events_by_simulation.pop(resolved_simulation_id, None)


def _request_payload() -> dict:
    if request.method == 'GET':
        return request.args.to_dict()
    return request.get_json(silent=True) or {}


def _find_report_task(
    task_manager: TaskManager,
    *,
    task_id: str = None,
    report_id: str = None,
    simulation_id: str = None,
    allowed_statuses=None,
):
    if task_id:
        task = task_manager.get_task(task_id)
        if task and task.task_type == "report_generate":
            return task
        return None

    allowed = set(allowed_statuses or [])
    tasks = task_manager.list_tasks(task_type="report_generate")
    for task_dict in tasks:
        metadata = task_dict.get("metadata") or {}
        status = task_dict.get("status")
        if allowed and status not in allowed:
            continue
        if report_id and metadata.get("report_id") == report_id:
            return task_manager.get_task(task_dict["task_id"])
        if simulation_id and metadata.get("simulation_id") == simulation_id:
            return task_manager.get_task(task_dict["task_id"])
    return None


# ============== 报告生成接口 ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    生成模拟分析报告（异步任务）
    
    这是一个耗时操作，接口会立即返回task_id，
    使用 GET /api/report/generate/status 查询进度
    
    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",    // 必填，模拟ID
            "force_regenerate": false        // 可选，强制重新生成
        }
    
    返回：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "报告生成任务已启动"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Please provide simulation_id"
            }), 400
        
        force_regenerate = data.get('force_regenerate', False)
        requested_report_id = data.get('report_id')
        resume_existing = bool(data.get('resume_existing'))
        
        # 获取模拟信息
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"Simulation not found: {simulation_id}"
            }), 404
        
        existing_report = ReportManager.get_report_by_simulation(simulation_id)

        task_manager = TaskManager()
        active_task = _find_report_task(
            task_manager,
            simulation_id=simulation_id,
            allowed_statuses=[TaskStatus.PENDING.value, TaskStatus.PROCESSING.value],
        )
        if active_task:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "report_id": active_task.metadata.get("report_id"),
                    "task_id": active_task.task_id,
                    "status": active_task.status.value,
                    "message": active_task.message or "Report generation is already in progress.",
                    "already_running": True,
                    "already_generated": False,
                }
            })

        # 检查是否已有报告
        if not force_regenerate:
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "message": "Report already exists",
                        "already_generated": True
                    }
                })
        
        # 获取项目信息
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project not found: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Missing graph ID. Make sure the graph has been built."
            }), 400
        
        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "Missing simulation requirement"
            }), 400
        
        import uuid
        report_to_resume = None
        if resume_existing:
            if requested_report_id:
                report_to_resume = ReportManager.get_report(requested_report_id)
                if not report_to_resume:
                    return jsonify({
                        "success": False,
                        "error": f"Report not found: {requested_report_id}"
                    }), 404
                if report_to_resume.simulation_id != simulation_id:
                    return jsonify({
                        "success": False,
                        "error": "The requested report does not belong to this simulation."
                    }), 400
            else:
                report_to_resume = existing_report

            if report_to_resume and report_to_resume.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": report_to_resume.report_id,
                        "status": "completed",
                        "message": "Report already exists",
                        "already_generated": True
                    }
                })

        should_resume = report_to_resume is not None
        report_id = report_to_resume.report_id if should_resume else f"report_{uuid.uuid4().hex[:12]}"
        
        # 创建异步任务
        task_id = task_manager.create_task(
            task_type="report_generate",
            metadata={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "report_id": report_id,
                "resume_existing": should_resume,
            }
        )
        cancel_event = threading.Event()
        _register_report_cancel_event(task_id, report_id, simulation_id, cancel_event)
        
        # 定义后台任务
        def run_generate():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="Resuming Report Agent..." if should_resume else "Initializing Report Agent..."
                )
                
                # 创建Report Agent
                agent = ReportAgent(
                    graph_id=graph_id,
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement
                )
                
                # 进度回调
                def progress_callback(stage, progress, message):
                    task_manager.update_task(
                        task_id,
                        progress=progress,
                        message=f"[{stage}] {message}"
                    )
                
                # 生成报告（传入预先生成的 report_id）
                report = agent.generate_report(
                    progress_callback=progress_callback,
                    report_id=report_id,
                    cancel_event=cancel_event,
                    resume_existing=should_resume,
                )
                
                # 保存报告
                ReportManager.save_report(report)
                
                if report.status == ReportStatus.COMPLETED:
                    task_manager.complete_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "completed"
                        }
                    )
                elif report.status == ReportStatus.CANCELED:
                    task_manager.cancel_task(
                        task_id,
                        message="Report generation canceled. The current work unit has stopped."
                    )
                    task_manager.update_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "canceled"
                        }
                    )
                else:
                    task_manager.fail_task(task_id, report.error or "Report generation failed")
                
            except OperationCanceledError as e:
                logger.info("Report generation canceled: %s", str(e))
                task_manager.cancel_task(
                    task_id,
                    message=str(e) or "Report generation canceled."
                )
            except Exception as e:
                logger.error("Report generation failed: %s", str(e))
                task_manager.fail_task(task_id, str(e))
            finally:
                _clear_report_cancel_event(
                    task_id=task_id,
                    report_id=report_id,
                    simulation_id=simulation_id,
                )
        
        # 启动后台线程
        thread = threading.Thread(target=run_generate, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": (
                    "Report generation resumed. Check progress via /api/report/generate/status."
                    if should_resume
                    else "Report generation started. Check progress via /api/report/generate/status."
                ),
                "already_generated": False
            }
        })
        
    except Exception as e:
        logger.error("Failed to start report generation: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['GET', 'POST'])
def get_generate_status():
    """
    查询报告生成任务进度
    
    请求（JSON）：
        {
            "task_id": "task_xxxx",         // 可选，generate返回的task_id
            "simulation_id": "sim_xxxx"     // 可选，模拟ID
        }
    
    返回：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = _request_payload()
        
        task_id = data.get('task_id')
        report_id = data.get('report_id')
        simulation_id = data.get('simulation_id')

        task_manager = TaskManager()

        task = _find_report_task(
            task_manager,
            task_id=task_id,
            report_id=report_id,
            simulation_id=simulation_id,
            allowed_statuses={
                TaskStatus.PENDING.value,
                TaskStatus.PROCESSING.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELED.value,
            },
        )
        if task:
            task_dict = task.to_dict()
            metadata = task.metadata or {}
            task_dict["report_id"] = metadata.get("report_id")
            task_dict["simulation_id"] = metadata.get("simulation_id")
            return jsonify({
                "success": True,
                "data": task_dict
            })
        
        report = None
        if report_id:
            report = ReportManager.get_report(report_id)
            if report and not simulation_id:
                simulation_id = report.simulation_id
        elif simulation_id:
            report = ReportManager.get_report_by_simulation(simulation_id)
            if report:
                report_id = report.report_id

        if report:
            progress = ReportManager.get_progress(report.report_id) or {}
            status_value = report.status.value
            progress_value = progress.get("progress")
            if progress_value is None:
                progress_value = 100 if report.status == ReportStatus.COMPLETED else 0

            return jsonify({
                "success": True,
                "data": {
                    "task_id": None,
                    "report_id": report.report_id,
                    "simulation_id": report.simulation_id,
                    "status": status_value,
                    "progress": progress_value,
                    "message": progress.get("message") or (
                        "Report already generated"
                        if report.status == ReportStatus.COMPLETED
                        else report.error
                        or f"Report status: {status_value}"
                    ),
                    "already_completed": report.status == ReportStatus.COMPLETED,
                }
            })

        if not task_id and not report_id and not simulation_id:
            return jsonify({
                "success": False,
                "error": "Please provide task_id, report_id, or simulation_id"
            }), 400

        return jsonify({
            "success": False,
            "error": "No report generation task or report was found for this request."
        }), 404
        
    except Exception as e:
        logger.error("Failed to query task status: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@report_bp.route('/generate/cancel', methods=['POST'])
def cancel_generate_report():
    """Request cooperative cancellation for an active report generation task."""
    try:
        data = request.get_json() or {}
        task_id = data.get('task_id')
        report_id = data.get('report_id')
        simulation_id = data.get('simulation_id')

        if not task_id and not report_id and not simulation_id:
            return jsonify({
                "success": False,
                "error": "Please provide task_id, report_id, or simulation_id"
            }), 400

        task_manager = TaskManager()
        task = _find_report_task(
            task_manager,
            task_id=task_id,
            report_id=report_id,
            simulation_id=simulation_id,
            allowed_statuses={
                TaskStatus.PENDING.value,
                TaskStatus.PROCESSING.value,
                TaskStatus.CANCELED.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
            },
        )

        if task:
            metadata = task.metadata or {}
            task_id = task.task_id
            report_id = report_id or metadata.get("report_id")
            simulation_id = simulation_id or metadata.get("simulation_id")

        entry = _get_report_cancel_entry(
            task_id=task_id,
            report_id=report_id,
            simulation_id=simulation_id,
        )

        if entry is None:
            report = ReportManager.get_report(report_id) if report_id else None
            if report is None and simulation_id:
                report = ReportManager.get_report_by_simulation(simulation_id)

            if report and report.status == ReportStatus.CANCELED:
                return jsonify({
                    "success": True,
                    "data": {
                        "task_id": task_id,
                        "report_id": report.report_id,
                        "simulation_id": report.simulation_id,
                        "status": ReportStatus.CANCELED.value,
                        "message": report.error or "Report generation was already canceled."
                    }
                })

            if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                return jsonify({
                    "success": False,
                    "error": f"Report task is already {task.status.value}."
                }), 400

            return jsonify({
                "success": False,
                "error": "No active report generation task was found for this request."
            }), 404

        if entry["event"].is_set():
            return jsonify({
                "success": True,
                "data": {
                    "task_id": entry["task_id"],
                    "report_id": entry["report_id"],
                    "simulation_id": entry["simulation_id"],
                    "status": "cancel_requested",
                    "message": "Cancellation was already requested. Waiting for the current section to stop."
                }
            })

        entry["event"].set()
        task_manager.update_task(
            entry["task_id"],
            message="Cancellation requested. Waiting for the current section to stop."
        )

        return jsonify({
            "success": True,
            "data": {
                "task_id": entry["task_id"],
                "report_id": entry["report_id"],
                "simulation_id": entry["simulation_id"],
                "status": "cancel_requested",
                "message": "Cancellation requested. Poll /api/report/generate/status until the task reports canceled."
            }
        })

    except Exception as e:
        logger.error("Failed to cancel report generation: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告获取接口 ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    获取报告详情
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"Report not found: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict()
        })
        
    except Exception as e:
        logger.error("Failed to fetch report: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    根据模拟ID获取报告
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"No report exists yet for simulation: {simulation_id}",
                "has_report": False
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })
        
    except Exception as e:
        logger.error("Failed to fetch report: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    列出所有报告
    
    Query参数：
        simulation_id: 按模拟ID过滤（可选）
        limit: 返回数量限制（默认50）
    
    返回：
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)
        
        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })
        
    except Exception as e:
        logger.error("Failed to list reports: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    下载报告（Markdown格式）
    
    返回Markdown文件
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"Report not found: {report_id}"
            }), 404
        
        md_path = ReportManager._get_report_markdown_path(report_id)
        
        if not os.path.exists(md_path):
            # 如果MD文件不存在，生成一个临时文件
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(report.markdown_content)
                temp_path = f.name
            
            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f"{report_id}.md"
            )
        
        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.md"
        )
        
    except Exception as e:
        logger.error("Failed to download report: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """删除报告"""
    try:
        success = ReportManager.delete_report(report_id)
        
        if not success:
            return jsonify({
                "success": False,
                "error": f"Report not found: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "message": f"Report deleted: {report_id}"
        })
        
    except Exception as e:
        logger.error("Failed to delete report: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Report Agent对话接口 ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    与Report Agent对话
    
    Report Agent可以在对话中自主调用检索工具来回答问题
    
    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",        // 必填，模拟ID
            "message": "请解释一下舆情走向",    // 必填，用户消息
            "chat_history": [                   // 可选，对话历史
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }
    
    返回：
        {
            "success": true,
            "data": {
                "response": "Agent回复...",
                "tool_calls": [调用的工具列表],
                "sources": [信息来源]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Please provide simulation_id"
            }), 400
        
        if not message:
            return jsonify({
                "success": False,
                "error": "Please provide message"
            }), 400
        
        # 获取模拟和项目信息
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"Simulation not found: {simulation_id}"
            }), 404
        
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project not found: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Missing graph ID"
            }), 400
        
        simulation_requirement = project.simulation_requirement or ""
        
        # 创建Agent并进行对话
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement
        )
        
        result = agent.chat(message=message, chat_history=chat_history)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error("Chat failed: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告进度与分章节接口 ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    获取报告生成进度（实时）
    
    返回：
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "正在生成章节: 关键发现",
                "current_section": "关键发现",
                "completed_sections": ["执行摘要", "模拟背景"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)
        
        if not progress:
            return jsonify({
                "success": False,
                "error": f"Report not found or progress unavailable: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": progress
        })
        
    except Exception as e:
        logger.error("Failed to fetch report progress: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    获取已生成的章节列表（分章节输出）
    
    前端可以轮询此接口获取已生成的章节内容，无需等待整个报告完成
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## 执行摘要\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)
        
        # 获取报告状态
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })
        
    except Exception as e:
        logger.error("Failed to fetch report sections: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    获取单个章节内容
    
    返回：
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## 执行摘要\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)
        
        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": f"Section not found: section_{section_index:02d}.md"
            }), 404
        
        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })
        
    except Exception as e:
        logger.error("Failed to fetch section content: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告状态检查接口 ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    检查模拟是否有报告，以及报告状态
    
    用于前端判断是否解锁Interview功能
    
    返回：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None
        
        # 只有报告完成后才解锁interview
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })
        
    except Exception as e:
        logger.error("Failed to check report status: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent 日志接口 ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    获取 Report Agent 的详细执行日志
    
    实时获取报告生成过程中的每一步动作，包括：
    - 报告开始、规划开始/完成
    - 每个章节的开始、工具调用、LLM响应、完成
    - 报告完成或失败
    
    Query参数：
        from_line: 从第几行开始读取（可选，默认0，用于增量获取）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "执行摘要",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error("Failed to fetch agent log: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    获取完整的 Agent 日志（一次性获取全部）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error("Failed to fetch agent log stream: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 控制台日志接口 ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    获取 Report Agent 的控制台输出日志
    
    实时获取报告生成过程中的控制台输出（INFO、WARNING等），
    这与 agent-log 接口返回的结构化 JSON 日志不同，
    是纯文本格式的控制台风格日志。
    
    Query参数：
        from_line: 从第几行开始读取（可选，默认0，用于增量获取）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: 搜索完成: 找到 15 条相关事实",
                    "[19:46:14] INFO: 图谱搜索: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_console_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error("Failed to fetch console log: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    获取完整的控制台日志（一次性获取全部）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error("Failed to fetch console log stream: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 工具调用接口（供调试使用）==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    图谱搜索工具接口（供调试使用）
    
    请求（JSON）：
        {
            "graph_id": "mirofish_xxxx",
            "query": "搜索查询",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)
        
        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": "Please provide graph_id and query"
            }), 400
        
        from ..services.graph_tools import GraphToolsService
        from flask import current_app
        storage = current_app.extensions.get('neo4j_storage')
        if not storage:
            raise ValueError("GraphStorage not initialized — check Neo4j connection")
        tools = GraphToolsService(storage=storage)
        result = tools.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error("Graph search failed: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    图谱统计工具接口（供调试使用）
    
    请求（JSON）：
        {
            "graph_id": "mirofish_xxxx"
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Please provide graph_id"
            }), 400
        
        from ..services.graph_tools import GraphToolsService
        from flask import current_app
        storage = current_app.extensions.get('neo4j_storage')
        if not storage:
            raise ValueError("GraphStorage not initialized — check TASK-015 DI setup")
        tools = GraphToolsService(storage=storage)
        result = tools.get_graph_statistics(graph_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error("Failed to fetch graph statistics: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
