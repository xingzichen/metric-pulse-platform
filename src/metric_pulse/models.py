"""数据库枚举与 ORM 模型。

执行状态、业务解决状态和审核状态相互独立：执行成功不代表数据已解决，人工驳回也不应计作
已核对。审计、证据和模型调用采用追加记录，便于追踪每个最终值的形成过程。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    """用户角色；权限大小由 security.ROLE_ORDER 定义。"""

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"


class FileStatus(StrEnum):
    """上传文件从接收、结构识别到可创建任务的生命周期。"""

    UPLOADED = "UPLOADED"
    ANALYZING = "ANALYZING"
    READY = "READY"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    FAILED = "FAILED"


class TaskStatus(StrEnum):
    """任务控制状态；PAUSING/STOPPING 表示等待当前单元安全收尾。"""

    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_ERRORS = "SUCCEEDED_WITH_ERRORS"
    FAILED = "FAILED"
    DELETED = "DELETED"


class UnitStatus(StrEnum):
    """单个采集单元的执行状态，与解决和审核状态无关。"""

    PENDING = "PENDING"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    DISCARDED = "DISCARDED"


class ResolutionStatus(StrEnum):
    """目标字段是否被可靠解决的业务语义。"""

    NOT_EVALUATED = "NOT_EVALUATED"
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class RiskLevel(StrEnum):
    """用于人工队列排序和自动审核策略的风险级别。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewStatus(StrEnum):
    """审核决策；REJECTED 是重采指令，不属于已完成审核统计。"""

    UNREVIEWED = "UNREVIEWED"
    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVED = "APPROVED"
    CORRECTED = "CORRECTED"
    CONFIRMED_UNRESOLVED = "CONFIRMED_UNRESOLVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class ExportStatus(StrEnum):
    """导出文件构建状态；任务变化后 READY 导出会转为 STALE。"""

    PENDING = "PENDING"
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"


class User(Base):
    """本地用户账户；仅保存密码哈希，不保存明文。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(32), default=Role.ADMIN)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginSession(Base):
    """服务端登录会话，主键本身即浏览器 Cookie 中的高熵令牌。"""

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user: Mapped[User] = relationship()


class FileRecord(Base):
    """上传文件元数据及确定性/视觉识别结果。"""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(800), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default=FileStatus.UPLOADED, index=True)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    sheets: Mapped[list[WorkbookSheet]] = relationship(back_populates="file", cascade="all, delete-orphan")


class WorkbookSheet(Base):
    """工作表识别结果的规范化副本，便于查询和模板匹配。"""

    __tablename__ = "workbook_sheets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer)
    header_row: Mapped[int] = mapped_column(Integer)
    data_start_row: Mapped[int] = mapped_column(Integer)
    max_row: Mapped[int] = mapped_column(Integer)
    max_column: Mapped[int] = mapped_column(Integer)
    headers: Mapped[list] = mapped_column(JSON)
    display_headers: Mapped[list] = mapped_column(JSON)
    descriptor_fields: Mapped[list] = mapped_column(JSON)
    target_fields: Mapped[list] = mapped_column(JSON)
    business_key_fields: Mapped[list] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    file: Mapped[FileRecord] = relationship(back_populates="sheets")

    __table_args__ = (Index("ix_workbook_sheet_file_position", "file_id", "position", unique=True),)


class TemplateVersion(Base):
    """可发布、可冻结的工作簿采集模板版本。"""

    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    schema: Mapped[dict] = mapped_column(JSON)
    structure_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CollectionTask(Base):
    """一次针对上传文件的采集计划和聚合状态。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    template_version_id: Mapped[str | None] = mapped_column(ForeignKey("template_versions.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.DRAFT, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    run_version: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    records: Mapped[list[DataRecord]] = relationship(back_populates="task", cascade="all, delete-orphan")
    units: Mapped[list[CollectionUnit]] = relationship(back_populates="task", cascade="all, delete-orphan")


class DataRecord(Base):
    """源工作簿一行的原始值和规划时冻结的 RowContract。"""

    __tablename__ = "records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(500), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    business_key: Mapped[str] = mapped_column(String(64), index=True)
    raw_data: Mapped[dict] = mapped_column(JSON)
    row_contract: Mapped[dict] = mapped_column(JSON)
    task: Mapped[CollectionTask] = relationship(back_populates="records")

    __table_args__ = (Index("ix_record_task_sheet_row", "task_id", "sheet_name", "source_row", unique=True),)


class CollectionUnit(Base):
    """可租用、可重试、可审核的最小采集工作单元。

    ``suggestion`` 是系统建议，``final_values`` 仅在审核后产生；``version`` 用于审核乐观锁，
    ``run_version`` 防止上一轮 worker 回写当前任务。
    """

    __tablename__ = "collection_units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    run_version: Mapped[int] = mapped_column(Integer)
    target_fields: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default=UnitStatus.PENDING, index=True)
    resolution_status: Mapped[str] = mapped_column(
        String(40), default=ResolutionStatus.NOT_EVALUATED, index=True
    )
    resolution_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    review_status: Mapped[str] = mapped_column(String(40), default=ReviewStatus.UNREVIEWED, index=True)
    review_required: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default=RiskLevel.HIGH, index=True)
    validation_version: Mapped[str] = mapped_column(String(40), default="resolution-v1")
    review_policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    task: Mapped[CollectionTask] = relationship(back_populates="units")
    record: Mapped[DataRecord] = relationship()


class CollectionAttempt(Base):
    """每次单元执行尝试的时间、状态和错误摘要。"""

    __tablename__ = "collection_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40))
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RowSearchAttempt(Base):
    """一次按行搜索的查询词、候选列表和时间信息。"""

    __tablename__ = "row_search_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(80), default="searxng")
    status: Mapped[str] = mapped_column(String(40))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceAcquisitionAttempt(Base):
    """直链或搜索降级的最终来源获取路由及匹配诊断。"""

    __tablename__ = "source_acquisition_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"), index=True)
    search_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("row_search_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    route: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    input_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    normalized_url: Mapped[str | None] = mapped_column(String(2000), nullable=True, index=True)
    final_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    persistent_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    match_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelCall(Base):
    """单次 SYNTHESIZE/VERIFY 模型调用的不可逆审计摘要。"""

    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceSnapshot(Base):
    """按规范 URL 与内容摘要去重的来源证据快照。"""

    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    normalized_url: Mapped[str] = mapped_column(String(2000), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class UnitSourceLink(Base):
    """单元与来源快照的多对多关系，记录排序、是否采用和定位信息。"""

    __tablename__ = "unit_source_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="CASCADE"), index=True
    )
    search_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("row_search_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    locator: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (Index("ix_unit_source_link_unique", "unit_id", "snapshot_id", unique=True),)


class Evidence(Base):
    """面向审核界面保存的逐单元证据；允许保留未被采用的候选。"""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"))
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_object_key: Mapped[str | None] = mapped_column(String(800), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewPolicy(Base):
    """已版本化的低风险自动审核规则及抽样质量统计。"""

    __tablename__ = "review_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_rate: Mapped[float] = mapped_column(Float, default=0.05)
    max_sample_error_rate: Mapped[float] = mapped_column(Float, default=0.02)
    sample_total: Mapped[int] = mapped_column(Integer, default=0)
    sample_errors: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_review_policy_name_version", "name", "version", unique=True),)


class ReviewBatch(Base):
    """批量审核预览令牌及冻结的单元版本快照。"""

    __tablename__ = "review_batches"

    token: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    unit_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="PREVIEWED")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewDecision(Base):
    """不可变人工/策略审核决策，保存前后值和当时单元版本。"""

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("collection_units.id", ondelete="CASCADE"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(40))
    before_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_version: Mapped[int] = mapped_column(Integer)
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_policies.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ExportJob(Base):
    """基于特定任务版本构建的 Excel 与未解决报告。"""

    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(40), default=ExportStatus.PENDING)
    task_version: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str | None] = mapped_column(String(800), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unresolved_object_key: Mapped[str | None] = mapped_column(String(800), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskEvent(Base):
    """供任务时间线和 SSE 增量读取的领域事件。"""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditLog(Base):
    """跨资源的操作者审计记录。"""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
