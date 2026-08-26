"""Metric Pulse FastAPI 应用及 HTTP 接口。

本模块负责认证授权、参数校验、资源查找、状态冲突转换和响应视图。采集、审核、导出等
规则分别委托给 service/processor 模块，避免路由函数成为第二套业务实现。长时间采集在
响应之后运行，任务的真实进度始终以数据库状态为准。
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .celery_app import dispatch_task
from .config import get_settings
from .dataset_profiles import excluded_sheet_policy, has_locked_dataset_profile
from .db import SessionLocal, create_schema, get_session
from .models import (
    AuditLog,
    CollectionAttempt,
    CollectionTask,
    CollectionUnit,
    Evidence,
    ExportJob,
    ExportStatus,
    FileRecord,
    FileStatus,
    LoginSession,
    ReviewDecision,
    ReviewPolicy,
    ReviewStatus,
    Role,
    RowSearchAttempt,
    SourceAcquisitionAttempt,
    TaskEvent,
    TaskStatus,
    TemplateVersion,
    UnitStatus,
    User,
    WorkbookSheet,
)
from .omlx import OMLXClient
from .processor import process_task_sync
from .review_batch import commit_review_preview, create_review_preview
from .review_service import ReviewConflict, build_export, export_readiness, review_unit
from .schemas import (
    BulkReviewCommitRequest,
    BulkReviewPreviewRequest,
    LoginRequest,
    ReviewPolicyCreate,
    ReviewRequest,
    TaskControl,
    TaskCreate,
    TemplateCreate,
    UserCreate,
    UserView,
)
from .security import (
    bootstrap_admin,
    create_login_session,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from .source_pipeline import normalize_source_url
from .state_machine import InvalidTransition
from .storage import FileObjectStore
from .task_service import (
    audit,
    change_task_status,
    plan_task,
    refresh_stats,
    soft_delete_task,
    start_task,
)
from .template_service import create_template_from_file, publish_template
from .views import file_view, sheet_view, task_view, unit_view
from .workbook import analyze_workbook, render_sheet_preview

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """初始化开发数据库结构和首个管理员；生产结构由 Alembic 管理。"""

    if settings.env != "production":
        create_schema()
    with SessionLocal() as db:
        bootstrap_admin(db)
    yield


app = FastAPI(
    title="Metric Pulse Platform API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """复用调用方请求 ID 或生成新 ID，并回写响应方便跨层排障。"""

    import uuid

    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def require_task(db: Session, task_id: str) -> CollectionTask:
    """读取未软删除任务，并统一转换为 404。"""

    task = db.get(CollectionTask, task_id)
    if not task or task.status == TaskStatus.DELETED:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def require_file(db: Session, file_id: str) -> FileRecord:
    """读取上传文件，并统一转换为 404。"""

    file = db.get(FileRecord, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return file


def persist_analysis(db: Session, file: FileRecord, analysis: dict[str, Any]) -> None:
    """同时更新文件 JSON 分析和规范化工作表表，保持两种读取方式一致。"""

    # SQLAlchemy JSON 列不会感知嵌套对象原地修改，深拷贝并 flag_modified 才能可靠持久化。
    file.analysis = copy.deepcopy(analysis)
    flag_modified(file, "analysis")
    file.status = FileStatus.NEEDS_CONFIRMATION if analysis["needs_confirmation"] else FileStatus.READY
    db.query(WorkbookSheet).filter(WorkbookSheet.file_id == file.id).delete()
    for item in analysis["sheets"]:
        db.add(
            WorkbookSheet(
                file_id=file.id,
                name=item["name"],
                position=item["position"],
                header_row=item["header_row"],
                data_start_row=item["data_start_row"],
                max_row=item["max_row"],
                max_column=item["max_column"],
                headers=item["headers"],
                display_headers=item["display_headers"],
                descriptor_fields=item["descriptor_fields"],
                target_fields=item["target_fields"],
                business_key_fields=item["business_key_fields"],
                confidence=item["confidence"],
                needs_confirmation=item["needs_confirmation"],
                profile={
                    "mode": item["mode"],
                    "fieldStats": item["field_stats"],
                    "excluded": bool(item.get("excluded")),
                    "exclusionReason": item.get("exclusion_reason"),
                },
            )
        )
    db.commit()


async def vision_recognize_file(file_id: str) -> None:
    """逐工作表执行可选视觉复核；失败时保留确定性分析并要求人工确认。

    模型提议的所有字段必须已经存在于确定性表头中，且无冲突时才覆盖字段角色。模型不可
    增加字段或坐标，这一边界保证视觉识别只是辅助而非结构真相。
    """

    if not settings.vision_analysis_enabled:
        return
    with SessionLocal() as db:
        file = db.get(FileRecord, file_id)
        if not file or not file.analysis:
            return
        source = FileObjectStore().path(file.object_key)
        client = OMLXClient()
        changed = False
        processable_sheets = []
        for sheet in file.analysis["sheets"]:
            exclusion = excluded_sheet_policy(sheet["name"])
            if exclusion:
                sheet["vision"] = {
                    "skipped": True,
                    "reason": exclusion,
                    "roleOverrideLocked": True,
                }
                sheet["needs_confirmation"] = False
                changed = True
            elif has_locked_dataset_profile(sheet["name"], sheet["headers"]):
                # 专用 Profile 的字段角色已经由业务和确定性代码完整定义。继续把截图送入唯一
                # 的 OMLX 通道不会改变结果，只会推迟真实采集，因此直接记录为契约锁定跳过。
                sheet["vision"] = {
                    "skipped": True,
                    "reason": {
                        "code": "PROFILE_CONTRACT_LOCKED",
                        "label": "业务采集契约已锁定",
                    },
                    "roleOverrideLocked": True,
                }
                sheet["needs_confirmation"] = False
                changed = True
            else:
                processable_sheets.append(sheet)
        if not processable_sheets:
            file.analysis["needs_confirmation"] = False
            persist_analysis(db, file, file.analysis)
            return
        try:
            await client.health()
        except Exception as exc:
            error = f"OMLX preflight failed: {exc}"
            for sheet in processable_sheets:
                sheet["vision"] = {"error": error, "valid": False}
                sheet["needs_confirmation"] = True
            file.analysis["needs_confirmation"] = any(
                sheet["needs_confirmation"] for sheet in file.analysis["sheets"]
            )
            persist_analysis(db, file, file.analysis)
            return
        for sheet in processable_sheets:
            try:
                preview = render_sheet_preview(source, sheet["name"])
                proposal = await client.analyze_sheet(
                    structure={
                        key: sheet[key]
                        for key in (
                            "name",
                            "headers",
                            "display_headers",
                            "header_row",
                            "data_rows",
                            "descriptor_fields",
                            "target_fields",
                            "business_key_fields",
                            "mode",
                        )
                    },
                    preview=preview,
                )
                headers = set(sheet["headers"])
                fields_valid = all(
                    set(proposal.get(key, [])) <= headers
                    for key in ("descriptor_fields", "target_fields", "business_key_fields")
                )
                profile_locked = has_locked_dataset_profile(sheet["name"], sheet["headers"])
                sheet["vision"] = {
                    "proposal": proposal,
                    "valid": fields_valid,
                    "roleOverrideLocked": profile_locked,
                }
                if fields_valid and not proposal.get("conflicts"):
                    if not profile_locked:
                        for key in (
                            "descriptor_fields",
                            "target_fields",
                            "business_key_fields",
                            "mode",
                        ):
                            if key in proposal:
                                sheet[key] = proposal[key]
                        sheet["confidence"] = min(
                            0.99,
                            float(proposal.get("confidence", 0.8)),
                        )
                        sheet["needs_confirmation"] = sheet["confidence"] < 0.9
                else:
                    sheet["needs_confirmation"] = True
                changed = True
            except Exception as exc:
                sheet["vision"] = {"error": str(exc), "valid": False}
                sheet["needs_confirmation"] = True
                changed = True
        if changed:
            file.analysis["needs_confirmation"] = any(
                sheet["needs_confirmation"] for sheet in file.analysis["sheets"]
            )
            persist_analysis(db, file, file.analysis)


# ---- 健康检查与模型预检 -------------------------------------------------

@app.get("/health/live")
def live() -> dict[str, Any]:
    return {"status": "ok", "version": app.version}


@app.get("/health/ready")
def ready(db: Session = Depends(get_session)) -> dict[str, Any]:
    db.scalar(select(func.count()).select_from(User))
    return {"status": "ready", "database": True}


@app.get("/api/v1/system/model-health")
async def model_health(_: User = Depends(require_role(Role.ADMIN))) -> dict[str, Any]:
    try:
        return await OMLXClient().health()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": settings.omlx_model}


async def require_collection_provider_ready() -> None:
    """在任务状态发生变化前检查模型，失败时不留下 QUEUED/RUNNING 半状态。"""

    try:
        await OMLXClient().health()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Collection model is unavailable: {exc}",
        ) from exc


# ---- 登录与会话 ---------------------------------------------------------

@app.post("/api/v1/auth/login", response_model=UserView)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_session)) -> User:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    login_session = create_login_session(db, user)
    response.set_cookie(
        settings.session_cookie_name,
        login_session.id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return user


@app.post("/api/v1/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        login_session = db.get(LoginSession, token)
        if login_session and login_session.user_id == user.id:
            db.delete(login_session)
            db.commit()
    response.delete_cookie(settings.session_cookie_name)


@app.get("/api/v1/auth/me", response_model=UserView)
def me(user: User = Depends(get_current_user)) -> User:
    return user


# ---- 文件上传、确定性识别与视觉复核 -------------------------------------

@app.get("/api/v1/files")
def list_files(db: Session = Depends(get_session), _: User = Depends(get_current_user)) -> dict[str, Any]:
    files = db.scalars(select(FileRecord).order_by(desc(FileRecord.created_at))).all()
    return {"items": [file_view(item) for item in files], "total": len(files)}


@app.post("/api/v1/files", status_code=201)
async def upload_file(
    background: BackgroundTasks,
    upload: Annotated[UploadFile, File(...)],
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    """限制大小并校验 XLSX ZIP 签名，保存对象后立即执行确定性结构分析。"""

    if not upload.filename or not upload.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=415, detail="Only .xlsx is supported")
    data = await upload.read((settings.max_upload_mb + 1) * 1024 * 1024)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    if not data.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="Invalid XLSX signature")
    key, digest = FileObjectStore().put_bytes(data, namespace="uploads", suffix=".xlsx")
    record = FileRecord(
        owner_id=user.id,
        original_name=Path(upload.filename).name,
        content_hash=digest,
        object_key=key,
        size=len(data),
        status=FileStatus.ANALYZING,
    )
    db.add(record)
    db.flush()
    try:
        analysis = analyze_workbook(FileObjectStore().path(key))
        persist_analysis(db, record, analysis)
    except Exception as exc:
        record.status = FileStatus.FAILED
        record.error = str(exc)
        db.commit()
        raise HTTPException(status_code=422, detail=f"Workbook analysis failed: {exc}") from exc
    audit(
        db,
        actor_id=user.id,
        action="file.upload",
        resource_type="file",
        resource_id=record.id,
        after={"name": record.original_name, "hash": digest},
    )
    db.commit()
    if settings.vision_analysis_enabled:
        background.add_task(vision_recognize_file, record.id)
    return file_view(record, include_analysis=True)


@app.get("/api/v1/files/{file_id}")
def get_file(
    file_id: str, db: Session = Depends(get_session), _: User = Depends(get_current_user)
) -> dict[str, Any]:
    file = require_file(db, file_id)
    return {
        **file_view(file, include_analysis=True),
        "sheets": [sheet_view(sheet) for sheet in file.sheets],
    }


@app.post("/api/v1/files/{file_id}/recognize", status_code=202)
async def recognize_file(
    file_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, str]:
    require_file(db, file_id)
    background.add_task(vision_recognize_file, file_id)
    return {"status": "accepted"}


@app.get("/api/v1/files/{file_id}/preview/{sheet_name}")
def sheet_preview(
    file_id: str,
    sheet_name: str,
    db: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> Response:
    file = require_file(db, file_id)
    if not file.analysis or sheet_name not in {item["name"] for item in file.analysis["sheets"]}:
        raise HTTPException(status_code=404, detail="Sheet not found")
    data = render_sheet_preview(FileObjectStore().path(file.object_key), sheet_name)
    return Response(content=data, media_type="image/png")


# ---- 任务规划与控制 -----------------------------------------------------

@app.get("/api/v1/tasks")
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    query = select(CollectionTask).where(CollectionTask.status != TaskStatus.DELETED)
    if status_filter:
        query = query.where(CollectionTask.status == status_filter)
    tasks = db.scalars(query.order_by(desc(CollectionTask.created_at))).all()
    return {"items": [task_view(item) for item in tasks], "total": len(tasks)}


@app.post("/api/v1/tasks", status_code=201)
async def create_task(
    payload: TaskCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    """使用显式数据集选择或识别建议规划任务，可选在同一请求后启动。"""

    if payload.start_immediately:
        await require_collection_provider_ready()
    file = require_file(db, payload.file_id)
    if file.status not in {FileStatus.READY, FileStatus.NEEDS_CONFIRMATION}:
        raise HTTPException(status_code=409, detail="File is not ready")
    datasets = [item.model_dump() for item in payload.datasets]
    if not datasets:
        datasets = [
            {
                "sheet_name": sheet["name"],
                "descriptor_fields": sheet["descriptor_fields"],
                "target_fields": sheet["target_fields"],
                "business_key_fields": sheet["business_key_fields"],
                "mode": sheet["mode"],
            }
            for sheet in file.analysis["sheets"]
            if sheet["target_fields"]
        ]
    analysis_by_name = {sheet["name"]: sheet for sheet in file.analysis["sheets"]}
    for dataset in datasets:
        sheet = analysis_by_name.get(dataset["sheet_name"])
        exclusion = excluded_sheet_policy(sheet["name"]) if sheet else None
        if exclusion:
            raise HTTPException(
                status_code=422,
                detail=f"工作表 {sheet['name']!r} 不由本平台处理: {exclusion['label']}",
            )
    task = CollectionTask(
        owner_id=user.id,
        file_id=file.id,
        name=payload.name,
        config={"datasets": datasets},
    )
    db.add(task)
    db.commit()
    try:
        plan_task(db, task)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.start_immediately:
        start_task(db, task, user)
        if settings.eager_tasks:
            background.add_task(process_task_sync, task.id)
        else:
            dispatch_task(task.id)
    return task_view(task, detail=True)


@app.get("/api/v1/tasks/{task_id}")
def get_task(
    task_id: str, db: Session = Depends(get_session), _: User = Depends(get_current_user)
) -> dict[str, Any]:
    task = require_task(db, task_id)
    refresh_stats(db, task)
    db.commit()
    return task_view(task, detail=True)


def apply_control(
    db: Session,
    task: CollectionTask,
    payload: TaskControl,
    target: TaskStatus,
    user: User,
) -> None:
    """处理乐观版本冲突并把状态机异常转换为 HTTP 409。"""

    if payload.expected_version is not None and payload.expected_version != task.version:
        raise HTTPException(status_code=409, detail="Task version conflict")
    try:
        change_task_status(db, task, target, actor=user)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/tasks/{task_id}/start", status_code=202)
async def start(
    task_id: str,
    payload: TaskControl,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    await require_collection_provider_ready()
    task = require_task(db, task_id)
    if payload.expected_version is not None and payload.expected_version != task.version:
        raise HTTPException(status_code=409, detail="Task version conflict")
    try:
        start_task(db, task, user)
    except (InvalidTransition, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if settings.eager_tasks:
        background.add_task(process_task_sync, task.id)
    else:
        dispatch_task(task.id)
    return task_view(task)


@app.post("/api/v1/tasks/{task_id}/pause", status_code=202)
def pause(
    task_id: str,
    payload: TaskControl,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    task = require_task(db, task_id)
    target = TaskStatus.PAUSING if task.status == TaskStatus.RUNNING else TaskStatus.PAUSED
    apply_control(db, task, payload, target, user)
    return task_view(task)


@app.post("/api/v1/tasks/{task_id}/resume", status_code=202)
async def resume(
    task_id: str,
    payload: TaskControl,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    await require_collection_provider_ready()
    task = require_task(db, task_id)
    apply_control(db, task, payload, TaskStatus.QUEUED, user)
    if settings.eager_tasks:
        background.add_task(process_task_sync, task.id)
    else:
        dispatch_task(task.id)
    return task_view(task)


@app.post("/api/v1/tasks/{task_id}/stop", status_code=202)
def stop(
    task_id: str,
    payload: TaskControl,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    task = require_task(db, task_id)
    apply_control(db, task, payload, TaskStatus.STOPPING, user)
    # A queued or paused task has no worker to acknowledge STOPPING.
    if task.stats.get("running", 0) == 0:
        db.query(CollectionUnit).filter(
            CollectionUnit.task_id == task.id,
            CollectionUnit.status.in_(["PENDING", "FAILED_RETRYABLE"]),
        ).update({CollectionUnit.status: "DISCARDED"}, synchronize_session=False)
        change_task_status(db, task, TaskStatus.STOPPED, actor=user)
        refresh_stats(db, task)
        db.commit()
    return task_view(task)


@app.delete("/api/v1/tasks/{task_id}", status_code=204)
def delete_task(
    task_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> None:
    soft_delete_task(db, require_task(db, task_id), user)


@app.post("/api/v1/tasks/{task_id}/retry", status_code=202)
async def retry_failed(
    task_id: str,
    background: BackgroundTasks,
    payload: TaskControl | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    await require_collection_provider_ready()
    task = require_task(db, task_id)
    if payload and payload.expected_version is not None and payload.expected_version != task.version:
        raise HTTPException(status_code=409, detail="Task version conflict")
    try:
        start_task(db, task, user)
    except (InvalidTransition, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if settings.eager_tasks:
        background.add_task(process_task_sync, task.id)
    else:
        dispatch_task(task.id)
    return task_view(task)


# ---- 人工审核、批量审核与历史来源影响预览 -------------------------------

@app.get("/api/v1/tasks/{task_id}/review-queue")
def review_queue(
    task_id: str,
    review_status: str | None = Query(default=None, alias="reviewStatus"),
    resolution_status: str | None = Query(default=None, alias="resolutionStatus"),
    execution_status: str | None = Query(default=None, alias="executionStatus"),
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    query = select(CollectionUnit).where(
        CollectionUnit.task_id == task_id,
        CollectionUnit.status.in_([UnitStatus.SUCCEEDED, UnitStatus.FAILED_FINAL]),
    )
    if review_status:
        query = query.where(CollectionUnit.review_status == review_status)
    else:
        # 无历史状态筛选时返回真正待人工处理的行动队列；已完成项仍可通过显式状态查看。
        query = query.where(CollectionUnit.review_required.is_(True))
    if resolution_status:
        query = query.where(CollectionUnit.resolution_status == resolution_status)
    if execution_status:
        if execution_status not in {UnitStatus.SUCCEEDED, UnitStatus.FAILED_FINAL}:
            raise HTTPException(status_code=422, detail="Unsupported review execution status")
        query = query.where(CollectionUnit.status == execution_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    units = db.scalars(
        query.order_by(
            case((CollectionUnit.status == UnitStatus.FAILED_FINAL, 0), else_=1),
            CollectionUnit.id,
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return {"items": [unit_view(unit, detail=True) for unit in units], "total": total}


@app.get("/api/v1/review-units/{unit_id}")
def review_context(
    unit_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    unit = db.get(CollectionUnit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    evidence = db.scalars(select(Evidence).where(Evidence.unit_id == unit.id)).all()
    acquisitions = db.scalars(
        select(SourceAcquisitionAttempt)
        .where(SourceAcquisitionAttempt.unit_id == unit.id)
        .order_by(desc(SourceAcquisitionAttempt.started_at))
    ).all()
    searches = db.scalars(
        select(RowSearchAttempt)
        .where(RowSearchAttempt.unit_id == unit.id)
        .order_by(desc(RowSearchAttempt.started_at))
    ).all()
    attempts = db.scalars(
        select(CollectionAttempt)
        .where(CollectionAttempt.unit_id == unit.id)
        .order_by(desc(CollectionAttempt.started_at))
    ).all()
    history = db.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.unit_id == unit.id)
        .order_by(desc(ReviewDecision.created_at))
    ).all()
    return {
        **unit_view(unit, detail=True),
        "evidence": [
            {
                "id": item.id,
                "sourceUrl": item.source_url,
                "title": item.source_title,
                "locator": item.locator,
                "excerpt": item.excerpt,
                "metadata": item.metadata_json,
            }
            for item in evidence
        ],
        "acquisitionAttempts": [
            {
                "id": item.id,
                "route": item.route,
                "status": item.status,
                "reason": item.reason,
                "inputUrl": item.input_url,
                "normalizedUrl": item.normalized_url,
                "finalUrl": item.final_url,
                "contentHash": item.content_hash,
                "cacheHit": item.cache_hit,
                "persistentCacheHit": item.persistent_cache_hit,
                "matchStatus": item.match_status,
                "matchCount": item.match_count,
                "details": item.details,
                "startedAt": item.started_at,
                "endedAt": item.ended_at,
            }
            for item in acquisitions
        ],
        "rowSearchAttempts": [
            {
                "id": item.id,
                "query": item.query,
                "provider": item.provider,
                "status": item.status,
                "resultCount": item.result_count,
                "results": [
                    view
                    for result in item.results
                    if (view := row_search_result_view(result)) is not None
                ],
                "startedAt": item.started_at,
                "endedAt": item.ended_at,
            }
            for item in searches
        ],
        "collectionAttempts": [
            {
                "id": item.id,
                "step": item.step,
                "status": item.status,
                "inputSummary": item.input_summary,
                "outputSummary": item.output_summary,
                "model": item.model,
                "error": item.error,
                "startedAt": item.started_at,
                "endedAt": item.ended_at,
            }
            for item in attempts
        ],
        "history": [
            {
                "id": item.id,
                "decision": item.decision,
                "beforeValues": item.before_values,
                "afterValues": item.after_values,
                "comment": item.comment,
                "metadata": item.metadata_json,
                "createdAt": item.created_at,
            }
            for item in history
        ],
    }


def row_search_result_view(result: object) -> dict[str, Any] | None:
    """只向审核页暴露类型明确且可安全打开的历史搜索候选。"""

    if not isinstance(result, dict):
        return None
    url = result.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    engines = result.get("engines")
    return {
        "rank": result.get("rank") if isinstance(result.get("rank"), int) else None,
        "url": url,
        "title": result.get("title") if isinstance(result.get("title"), str) else None,
        "excerpt": result.get("excerpt") if isinstance(result.get("excerpt"), str) else None,
        "engines": (
            [engine for engine in engines if isinstance(engine, str)]
            if isinstance(engines, list)
            else []
        ),
    }


@app.get("/api/v1/tasks/{task_id}/source-repair-preview")
def source_repair_preview(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    """Read-only list of completed rows whose selected evidence bypassed an input source URL."""
    require_task(db, task_id)
    units = db.scalars(
        select(CollectionUnit).where(
            CollectionUnit.task_id == task_id,
            CollectionUnit.status == UnitStatus.SUCCEEDED,
        )
    ).all()
    unit_ids = [unit.id for unit in units]
    evidence_by_unit: dict[str, list[Evidence]] = {unit_id: [] for unit_id in unit_ids}
    if unit_ids:
        for item in db.scalars(select(Evidence).where(Evidence.unit_id.in_(unit_ids))).all():
            evidence_by_unit[item.unit_id].append(item)
    audited_units = (
        set(
            db.scalars(
                select(SourceAcquisitionAttempt.unit_id).where(SourceAcquisitionAttempt.unit_id.in_(unit_ids))
            ).all()
        )
        if unit_ids
        else set()
    )

    suspected: list[dict[str, Any]] = []
    for unit in units:
        input_url = next(
            (
                value
                for key, value in unit.record.raw_data.items()
                if key in {"source_url", "url", "link"} and isinstance(value, str) and value.strip()
            ),
            None,
        )
        if not input_url:
            continue
        selected = next(
            (
                item
                for item in evidence_by_unit.get(unit.id, [])
                if item.metadata_json.get("selected") is True and item.source_url
            ),
            None,
        )
        selected_url = selected.source_url if selected else None
        bypassed = selected_url is None or normalize_source_url(selected_url) != normalize_source_url(
            input_url
        )
        missing_route_audit = unit.id not in audited_units
        if not bypassed and not missing_route_audit:
            continue
        suspected.append(
            {
                "unitId": unit.id,
                "sheetName": unit.record.sheet_name,
                "sourceRow": unit.record.source_row,
                "inputUrl": input_url,
                "selectedUrl": selected_url,
                "suggestion": unit.suggestion,
                "reason": ("LEGACY_ROUTE_NOT_AUDITED" if missing_route_audit else "SELECTED_SOURCE_DIFFERS"),
                "canApply": False,
            }
        )
    return {
        "total": len(suspected),
        "items": suspected[:limit],
        "readOnly": True,
        "applyRequiresConfirmation": True,
    }


@app.post("/api/v1/review-units/{unit_id}")
def decide_review(
    unit_id: str,
    payload: ReviewRequest,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.REVIEWER)),
) -> dict[str, Any]:
    unit = db.get(CollectionUnit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    try:
        review_unit(
            db,
            unit=unit,
            actor=user,
            decision=ReviewStatus(payload.decision),
            expected_version=payload.expected_version,
            values=payload.values,
            comment=payload.comment,
        )
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return unit_view(unit, detail=True)


@app.post("/api/v1/tasks/{task_id}/reviews/bulk/preview")
def bulk_review_preview(
    task_id: str,
    payload: BulkReviewPreviewRequest,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.REVIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    try:
        batch = create_review_preview(
            db,
            task_id=task_id,
            actor=user,
            decision=payload.decision,
            unit_ids=payload.unit_ids,
            risk_levels=payload.risk_levels,
            comment=payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "previewToken": batch.token,
        "expiresAt": batch.expires_at,
        **batch.preview,
    }


@app.post("/api/v1/tasks/{task_id}/reviews/bulk/commit")
def bulk_review_commit(
    task_id: str,
    payload: BulkReviewCommitRequest,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.REVIEWER)),
) -> dict[str, int]:
    require_task(db, task_id)
    try:
        changed = commit_review_preview(
            db,
            token=payload.preview_token,
            task_id=task_id,
            actor=user,
        )
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"changed": changed}


# ---- 导出门禁与文件下载 -------------------------------------------------

@app.get("/api/v1/tasks/{task_id}/export-readiness")
def readiness_view(
    task_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    return export_readiness(db, task_id)


@app.get("/api/v1/tasks/{task_id}/exports")
def list_exports(
    task_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    jobs = db.scalars(
        select(ExportJob).where(ExportJob.task_id == task_id).order_by(desc(ExportJob.created_at))
    ).all()
    return {
        "items": [
            {
                "id": job.id,
                "status": job.status,
                "taskVersion": job.task_version,
                "contentHash": job.content_hash,
                "error": job.error,
                "hasUnresolvedReport": bool(job.unresolved_object_key),
                "createdAt": job.created_at,
            }
            for job in jobs
        ]
    }


@app.post("/api/v1/tasks/{task_id}/exports", status_code=201)
def create_export(
    task_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    task = require_task(db, task_id)
    try:
        job = build_export(db, task, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "error": job.error}


@app.get("/api/v1/exports/{export_id}/download")
def download_export(
    export_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> FileResponse:
    job = db.get(ExportJob, export_id)
    if not job or job.status != "READY" or not job.object_key:
        raise HTTPException(status_code=404, detail="Export not ready")
    path = FileObjectStore().path(job.object_key)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"metric-pulse-{job.task_id}.xlsx",
    )


@app.get("/api/v1/exports/{export_id}/unresolved-report")
def download_unresolved_report(
    export_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> FileResponse:
    job = db.get(ExportJob, export_id)
    if not job or job.status != ExportStatus.READY or not job.unresolved_object_key:
        raise HTTPException(status_code=404, detail="Unresolved report not ready")
    return FileResponse(
        FileObjectStore().path(job.unresolved_object_key),
        media_type="application/json",
        filename=f"metric-pulse-{job.task_id}-unresolved.json",
    )


# ---- 模板版本 -----------------------------------------------------------

@app.get("/api/v1/templates")
def list_templates(
    db: Session = Depends(get_session), _: User = Depends(require_role(Role.VIEWER))
) -> dict[str, Any]:
    items = db.scalars(select(TemplateVersion).order_by(desc(TemplateVersion.created_at))).all()
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "version": item.version,
                "status": item.status,
                "structureHash": item.structure_hash,
                "createdAt": item.created_at,
            }
            for item in items
        ]
    }


@app.post("/api/v1/templates", status_code=201)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.ADMIN)),
) -> dict[str, Any]:
    template = create_template_from_file(
        db, file=require_file(db, payload.file_id), name=payload.name, actor=user
    )
    return {"id": template.id, "name": template.name, "version": template.version}


@app.post("/api/v1/templates/{template_id}/publish")
def publish(
    template_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.ADMIN)),
) -> dict[str, str]:
    template = db.get(TemplateVersion, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    publish_template(db, template, user)
    return {"status": template.status}


# ---- 管理员：用户、审核策略与审计 ---------------------------------------

@app.get("/api/v1/admin/users")
def list_users(
    db: Session = Depends(get_session), _: User = Depends(require_role(Role.ADMIN))
) -> dict[str, Any]:
    users = db.scalars(select(User).order_by(User.username)).all()
    return {"items": [UserView.model_validate(user) for user in users]}


@app.get("/api/v1/admin/review-policies")
def list_review_policies(
    db: Session = Depends(get_session), _: User = Depends(require_role(Role.ADMIN))
) -> dict[str, Any]:
    policies = db.scalars(select(ReviewPolicy).order_by(desc(ReviewPolicy.created_at))).all()
    return {
        "items": [
            {
                "id": policy.id,
                "name": policy.name,
                "version": policy.version,
                "status": policy.status,
                "rules": policy.rules,
                "sampleRate": policy.sample_rate,
                "maxSampleErrorRate": policy.max_sample_error_rate,
                "sampleTotal": policy.sample_total,
                "sampleErrors": policy.sample_errors,
                "createdAt": policy.created_at,
                "publishedAt": policy.published_at,
            }
            for policy in policies
        ]
    }


@app.post("/api/v1/admin/review-policies", status_code=201)
def create_review_policy(
    payload: ReviewPolicyCreate,
    db: Session = Depends(get_session),
    actor: User = Depends(require_role(Role.ADMIN)),
) -> dict[str, Any]:
    version = (
        db.scalar(select(func.max(ReviewPolicy.version)).where(ReviewPolicy.name == payload.name)) or 0
    ) + 1
    policy = ReviewPolicy(
        name=payload.name,
        version=version,
        rules=payload.rules,
        sample_rate=payload.sample_rate,
        max_sample_error_rate=payload.max_sample_error_rate,
        created_by=actor.id,
    )
    db.add(policy)
    db.flush()
    audit(
        db,
        actor_id=actor.id,
        action="review_policy.create",
        resource_type="review_policy",
        resource_id=policy.id,
        after={"name": policy.name, "version": policy.version, "rules": policy.rules},
    )
    db.commit()
    return {"id": policy.id, "name": policy.name, "version": policy.version, "status": policy.status}


@app.post("/api/v1/admin/review-policies/{policy_id}/publish")
def publish_review_policy(
    policy_id: str,
    db: Session = Depends(get_session),
    actor: User = Depends(require_role(Role.ADMIN)),
) -> dict[str, Any]:
    policy = db.get(ReviewPolicy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Review policy not found")
    if policy.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft policies can be published")
    db.query(ReviewPolicy).filter(ReviewPolicy.status == "PUBLISHED").update(
        {ReviewPolicy.status: "DISABLED"}, synchronize_session=False
    )
    policy.status = "PUBLISHED"
    policy.published_at = datetime.now(UTC)
    audit(
        db,
        actor_id=actor.id,
        action="review_policy.publish",
        resource_type="review_policy",
        resource_id=policy.id,
        after={"status": policy.status, "version": policy.version},
    )
    db.commit()
    return {"id": policy.id, "status": policy.status, "publishedAt": policy.published_at}


@app.post("/api/v1/admin/users", response_model=UserView, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_session),
    actor: User = Depends(require_role(Role.ADMIN)),
) -> User:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="Username already exists")
    try:
        role = Role(payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid role") from exc
    user = User(username=payload.username, password_hash=hash_password(payload.password), role=role)
    db.add(user)
    db.flush()
    audit(
        db,
        actor_id=actor.id,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        after={"username": user.username, "role": user.role},
    )
    db.commit()
    return user


@app.get("/api/v1/admin/audit")
def list_audit(
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.ADMIN)),
) -> dict[str, Any]:
    items = db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)).all()
    return {
        "items": [
            {
                "id": item.id,
                "actorId": item.actor_id,
                "action": item.action,
                "resourceType": item.resource_type,
                "resourceId": item.resource_id,
                "before": item.before,
                "after": item.after,
                "createdAt": item.created_at,
            }
            for item in items
        ]
    }


# ---- 任务事件：普通轮询与 SSE 流 ----------------------------------------

@app.get("/api/v1/tasks/{task_id}/events")
def task_events(
    task_id: str,
    after: int = 0,
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    events = db.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id, TaskEvent.id > after)
        .order_by(TaskEvent.id)
        .limit(500)
    ).all()
    return {
        "items": [
            {
                "sequence": item.id,
                "type": item.event_type,
                "payload": item.payload,
                "createdAt": item.created_at,
            }
            for item in events
        ]
    }


@app.get("/api/v1/tasks/{task_id}/events/stream")
async def task_event_stream(
    task_id: str,
    after: int = 0,
    _: User = Depends(require_role(Role.VIEWER)),
) -> StreamingResponse:
    """按递增事件 ID 推送 SSE；心跳用于保持代理连接并触发断线检测。"""

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while True:
            with SessionLocal() as db:
                events = db.scalars(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id, TaskEvent.id > cursor)
                    .order_by(TaskEvent.id)
                    .limit(100)
                ).all()
                for item in events:
                    cursor = item.id
                    payload = json.dumps(
                        {"type": item.event_type, "payload": item.payload}, ensure_ascii=False
                    )
                    yield f"id: {item.id}\nevent: {item.event_type}\ndata: {payload}\n\n"
            yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


def run() -> None:
    """启动本地 API；代码变更通过显式安全重启加载，不启用热重载。"""

    uvicorn.run("metric_pulse.main:app", host="0.0.0.0", port=8000, reload=False)
