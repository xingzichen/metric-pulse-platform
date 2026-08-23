from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .celery_app import dispatch_task
from .config import get_settings
from .db import SessionLocal, create_schema, get_session
from .models import (
    AuditLog,
    CollectionTask,
    CollectionUnit,
    Evidence,
    ExportJob,
    FileRecord,
    FileStatus,
    LoginSession,
    ReviewDecision,
    ReviewStatus,
    Role,
    TaskEvent,
    TaskStatus,
    TemplateVersion,
    User,
    WorkbookSheet,
)
from .omlx import OMLXClient
from .processor import process_task_sync
from .review_service import ReviewConflict, build_export, export_readiness, review_unit
from .schemas import (
    BulkReviewRequest,
    LoginRequest,
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
    import uuid

    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def require_task(db: Session, task_id: str) -> CollectionTask:
    task = db.get(CollectionTask, task_id)
    if not task or task.status == TaskStatus.DELETED:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def require_file(db: Session, file_id: str) -> FileRecord:
    file = db.get(FileRecord, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return file


def persist_analysis(db: Session, file: FileRecord, analysis: dict[str, Any]) -> None:
    # SQLAlchemy JSON columns do not observe nested in-place mutations.
    file.analysis = copy.deepcopy(analysis)
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
                profile={"mode": item["mode"], "fieldStats": item["field_stats"]},
            )
        )
    db.commit()


async def vision_recognize_file(file_id: str) -> None:
    if not settings.vision_analysis_enabled:
        return
    with SessionLocal() as db:
        file = db.get(FileRecord, file_id)
        if not file or not file.analysis:
            return
        source = FileObjectStore().path(file.object_key)
        client = OMLXClient()
        changed = False
        for sheet in file.analysis["sheets"]:
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
                sheet["vision"] = {"proposal": proposal, "valid": fields_valid}
                if fields_valid and not proposal.get("conflicts"):
                    for key in ("descriptor_fields", "target_fields", "business_key_fields", "mode"):
                        if key in proposal:
                            sheet[key] = proposal[key]
                    sheet["confidence"] = min(0.99, float(proposal.get("confidence", 0.8)))
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
def create_task(
    payload: TaskCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
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
    if payload.expected_version is not None and payload.expected_version != task.version:
        raise HTTPException(status_code=409, detail="Task version conflict")
    try:
        change_task_status(db, task, target, actor=user)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/tasks/{task_id}/start", status_code=202)
def start(
    task_id: str,
    payload: TaskControl,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    task = require_task(db, task_id)
    if payload.expected_version is not None and payload.expected_version != task.version:
        raise HTTPException(status_code=409, detail="Task version conflict")
    try:
        start_task(db, task, user)
    except InvalidTransition as exc:
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
def resume(
    task_id: str,
    payload: TaskControl,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
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
def retry_failed(
    task_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
) -> dict[str, Any]:
    task = require_task(db, task_id)
    start_task(db, task, user)
    if settings.eager_tasks:
        background.add_task(process_task_sync, task.id)
    else:
        dispatch_task(task.id)
    return task_view(task)


@app.get("/api/v1/tasks/{task_id}/review-queue")
def review_queue(
    task_id: str,
    review_status: str | None = Query(default=None, alias="reviewStatus"),
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_session),
    _: User = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    require_task(db, task_id)
    query = select(CollectionUnit).where(CollectionUnit.task_id == task_id)
    if review_status:
        query = query.where(CollectionUnit.review_status == review_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    units = db.scalars(query.order_by(CollectionUnit.id).offset(offset).limit(limit)).all()
    return {"items": [unit_view(unit) for unit in units], "total": total}


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
        "history": [
            {
                "id": item.id,
                "decision": item.decision,
                "beforeValues": item.before_values,
                "afterValues": item.after_values,
                "comment": item.comment,
                "createdAt": item.created_at,
            }
            for item in history
        ],
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


@app.post("/api/v1/tasks/{task_id}/reviews/bulk")
def bulk_review(
    task_id: str,
    payload: BulkReviewRequest,
    db: Session = Depends(get_session),
    user: User = Depends(require_role(Role.REVIEWER)),
) -> dict[str, int]:
    require_task(db, task_id)
    units = db.scalars(
        select(CollectionUnit).where(
            CollectionUnit.task_id == task_id, CollectionUnit.id.in_(payload.unit_ids)
        )
    ).all()
    changed = 0
    for unit in units:
        review_unit(
            db,
            unit=unit,
            actor=user,
            decision=ReviewStatus(payload.decision),
            expected_version=unit.version,
            comment=payload.comment,
        )
        changed += 1
    return {"changed": changed}


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


@app.get("/api/v1/admin/users")
def list_users(
    db: Session = Depends(get_session), _: User = Depends(require_role(Role.ADMIN))
) -> dict[str, Any]:
    users = db.scalars(select(User).order_by(User.username)).all()
    return {"items": [UserView.model_validate(user) for user in users]}


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
    uvicorn.run("metric_pulse.main:app", host="0.0.0.0", port=8000, reload=False)
