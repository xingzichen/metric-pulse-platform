"""任务执行器与采集审计持久化。

处理器一次租用一个单元，提交 RUNNING 状态后才调用慢速外部能力。租约用于进程异常后的
遗留恢复；暂停只在当前单元结束后生效。结果落库时同时写入证据、来源快照、模型调用和
采集路由，再统一刷新任务统计。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .collector import Collector, OMLXCollector, SourceCooldownError, configured_collector
from .config import get_settings
from .models import (
    CollectionAttempt,
    CollectionTask,
    CollectionUnit,
    DataRecord,
    Evidence,
    ModelCall,
    ReviewStatus,
    RowSearchAttempt,
    SourceAcquisitionAttempt,
    SourceSnapshot,
    TaskStatus,
    UnitSourceLink,
    UnitStatus,
)
from .resolution import apply_resolution
from .review_policy import apply_published_policy
from .task_service import add_event, refresh_stats


def _normalized_url(value: str) -> str:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def persist_collection_audit(db: Session, unit: CollectionUnit, result) -> None:
    """把采集器返回的非业务值元数据写入规范化审计表。

    来源快照按“规范化 URL + 摘录哈希”去重；同一结果里可能同时出现普通文档证据和结构化
    候选证据，因此还要用 ``linked_snapshot_ids`` 避免重复插入唯一关联键。
    """

    search_attempt: RowSearchAttempt | None = None
    linked_snapshot_ids = set(
        db.scalars(select(UnitSourceLink.snapshot_id).where(UnitSourceLink.unit_id == unit.id)).all()
    )
    if result.search_attempt:
        item = result.search_attempt
        search_attempt = RowSearchAttempt(
            unit_id=unit.id,
            query=item["query"],
            provider=item.get("provider", "searxng"),
            status=item.get("status", "SUCCEEDED"),
            result_count=item.get("result_count", 0),
            results=item.get("results", []),
            started_at=item.get("started_at", datetime.now(UTC)),
            ended_at=item.get("ended_at"),
        )
        db.add(search_attempt)
        db.flush()
    if result.acquisition_attempt:
        item = result.acquisition_attempt
        details = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "route",
                "status",
                "reason",
                "input_url",
                "normalized_url",
                "final_url",
                "content_hash",
                "cache_hit",
                "persistent_cache_hit",
                "match_status",
                "match_count",
                "started_at",
                "ended_at",
            }
        }
        db.add(
            SourceAcquisitionAttempt(
                unit_id=unit.id,
                search_attempt_id=search_attempt.id if search_attempt else None,
                route=item["route"],
                status=item.get("status", "SUCCEEDED"),
                reason=item.get("reason"),
                input_url=item.get("input_url"),
                normalized_url=item.get("normalized_url"),
                final_url=item.get("final_url"),
                content_hash=item.get("content_hash"),
                cache_hit=item.get("cache_hit") is True,
                persistent_cache_hit=item.get("persistent_cache_hit") is True,
                match_status=item.get("match_status"),
                match_count=item.get("match_count", 0),
                details=details,
                started_at=item.get("started_at", datetime.now(UTC)),
                ended_at=item.get("ended_at"),
            )
        )
    for item in result.model_calls:
        db.add(
            ModelCall(
                unit_id=unit.id,
                phase=item["phase"],
                model=item["model"],
                status=item.get("status", "SUCCEEDED"),
                input_hash=item["input_hash"],
                output_summary=item.get("output_summary", {}),
                started_at=item.get("started_at", datetime.now(UTC)),
                ended_at=item.get("ended_at"),
            )
        )
    for evidence in result.evidence:
        if not evidence.source_url:
            continue
        normalized = _normalized_url(evidence.source_url)
        excerpt = evidence.excerpt or ""
        content_hash = hashlib.sha256(excerpt.encode()).hexdigest()
        snapshot_key = hashlib.sha256(f"{normalized}\n{content_hash}".encode()).hexdigest()
        snapshot = db.scalar(select(SourceSnapshot).where(SourceSnapshot.snapshot_key == snapshot_key))
        if snapshot is None:
            snapshot = SourceSnapshot(
                snapshot_key=snapshot_key,
                normalized_url=normalized,
                content_hash=content_hash,
                title=evidence.title,
                excerpt=evidence.excerpt,
                metadata_json=evidence.metadata,
            )
            db.add(snapshot)
            db.flush()
        if snapshot.id not in linked_snapshot_ids:
            rank = evidence.metadata.get("rank")
            db.add(
                UnitSourceLink(
                    unit_id=unit.id,
                    snapshot_id=snapshot.id,
                    search_attempt_id=search_attempt.id if search_attempt else None,
                    rank=int(rank) if isinstance(rank, int | str) and str(rank).isdigit() else None,
                    selected=evidence.metadata.get("selected") is True,
                    locator=evidence.locator,
                )
            )
            linked_snapshot_ids.add(snapshot.id)


def validate_production_collection_contract(result) -> None:
    """在落库前强制验证生产采集不变量。

    该门禁防止未来重构意外绕过直链优先、来源级图片表格预处理、固定双模型提取
    或指定本地模型。测试替身不受此约束，
    因为它们用于状态机测试而非生产采集。
    """

    acquisition = result.acquisition_attempt
    if not acquisition or acquisition.get("status") != "SUCCEEDED":
        raise ValueError("Production collection requires one successful source acquisition route")
    route = acquisition.get("route")
    if route not in {"DIRECT_LINK", "SEARCH_FALLBACK"}:
        raise ValueError("Production collection used an unknown source acquisition route")
    if route == "DIRECT_LINK" and result.search_attempt is not None:
        raise ValueError("Direct-link collection must not perform a row search")
    if route == "SEARCH_FALLBACK" and (
        not result.search_attempt or result.search_attempt.get("status") != "SUCCEEDED"
    ):
        raise ValueError("Search fallback requires one successful row search attempt")
    phases = [item.get("phase") for item in result.model_calls]
    if phases[-2:] != ["SYNTHESIZE", "VERIFY"] or any(phase != "VISION_TABLE" for phase in phases[:-2]):
        raise ValueError(
            "Production collection requires optional source-level VISION_TABLE calls followed by "
            "exactly SYNTHESIZE then VERIFY"
        )
    if any(item.get("model") != "Qwen3.8-27B-6bit" for item in result.model_calls):
        raise ValueError("Production collection used an unexpected model")


class TaskProcessor:
    """串行消费任务单元并维护租约、重试、审计和统计。"""

    def __init__(self, collector: Collector | None = None) -> None:
        self.collector = collector or configured_collector()
        self.enforce_quality_contract = isinstance(self.collector, OMLXCollector)

    async def process(self, db: Session, task_id: str, *, max_units: int | None = None) -> None:
        """处理任务直到完成、控制状态生效或达到测试限定单元数。"""

        task = db.get(CollectionTask, task_id)
        if not task or task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.RUNNING
            task.version += 1
            add_event(db, task.id, "task.status.changed", {"from": "QUEUED", "to": "RUNNING"})
            db.commit()
        # worker 重新接管任务时，先回收已经超过租约且没有正常收尾的遗留单元。
        settings = get_settings()
        now = datetime.now(UTC)
        recovered = (
            db.query(CollectionUnit)
            .filter(
                CollectionUnit.task_id == task.id,
                CollectionUnit.run_version == task.run_version,
                CollectionUnit.status.in_([UnitStatus.LEASED, UnitStatus.RUNNING]),
                CollectionUnit.leased_until.is_not(None),
                CollectionUnit.leased_until < now,
            )
            .update(
                {
                    CollectionUnit.status: UnitStatus.FAILED_RETRYABLE,
                    CollectionUnit.lease_owner: None,
                    CollectionUnit.leased_until: None,
                    CollectionUnit.next_attempt_at: now,
                    CollectionUnit.error: "Recovered expired worker lease",
                },
                synchronize_session=False,
            )
        )
        if recovered:
            add_event(db, task.id, "task.leases.recovered", {"count": recovered})
            db.commit()
        lease_owner = f"{socket.gethostname()}:{os.getpid()}"
        processed = 0
        while max_units is None or processed < max_units:
            # 每个单元开始前重新读取控制状态，因此暂停不会打断正在进行的模型调用。
            db.refresh(task)
            if task.status in {TaskStatus.PAUSING, TaskStatus.PAUSED}:
                if task.status == TaskStatus.PAUSING:
                    task.status = TaskStatus.PAUSED
                    task.version += 1
                    add_event(db, task.id, "task.status.changed", {"from": "PAUSING", "to": "PAUSED"})
                    db.commit()
                return
            if task.status in {TaskStatus.STOPPING, TaskStatus.STOPPED, TaskStatus.DELETED}:
                if task.status == TaskStatus.STOPPING:
                    task.status = TaskStatus.STOPPED
                    task.version += 1
                    db.query(CollectionUnit).filter(
                        CollectionUnit.task_id == task.id,
                        CollectionUnit.status.in_([UnitStatus.PENDING, UnitStatus.FAILED_RETRYABLE]),
                    ).update({CollectionUnit.status: UnitStatus.DISCARDED}, synchronize_session=False)
                    add_event(db, task.id, "task.status.changed", {"from": "STOPPING", "to": "STOPPED"})
                    refresh_stats(db, task)
                    db.commit()
                return
            # 数据库锁和租约共同防止多个 worker 取得同一个单元。SQLite 会忽略 skip_locked，
            # 但本地 eager 模式只有一个 worker；PostgreSQL 部署可真正跳过已锁行。
            unit = db.scalar(
                select(CollectionUnit)
                .where(
                    CollectionUnit.task_id == task.id,
                    CollectionUnit.run_version == task.run_version,
                    CollectionUnit.status.in_([UnitStatus.PENDING, UnitStatus.FAILED_RETRYABLE]),
                    (
                        CollectionUnit.next_attempt_at.is_(None)
                        | (CollectionUnit.next_attempt_at <= datetime.now(UTC))
                    ),
                )
                .join(DataRecord, DataRecord.id == CollectionUnit.record_id)
                .order_by(
                    func.coalesce(CollectionUnit.source_affinity_key, CollectionUnit.id),
                    DataRecord.source_row,
                    CollectionUnit.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not unit:
                retryable = db.scalar(
                    select(CollectionUnit.id)
                    .where(
                        CollectionUnit.task_id == task.id,
                        CollectionUnit.run_version == task.run_version,
                        CollectionUnit.status == UnitStatus.FAILED_RETRYABLE,
                    )
                    .limit(1)
                )
                if retryable:
                    await asyncio.sleep(1)
                    continue
                break
            if unit.review_status == ReviewStatus.REJECTED:
                # 驳回是重采指令而不是完成审核。历史 ReviewDecision 保留不可变，新结果重新
                # 从待审核开始，避免“驳回也算已核对”的统计错误。
                unit.review_status = ReviewStatus.UNREVIEWED
                unit.review_required = True
                unit.final_values = None
                unit.review_policy_id = None
                unit.review_sampled = False
            # 先单独提交租约，再进入 RUNNING。即使进程随后退出，其他 worker 也能在到期后恢复。
            unit.status = UnitStatus.LEASED
            unit.lease_owner = lease_owner
            unit.leased_until = datetime.now(UTC) + timedelta(seconds=settings.unit_lease_seconds)
            unit.next_attempt_at = None
            refresh_stats(db, task)
            db.commit()
            unit.status = UnitStatus.RUNNING
            unit.started_at = unit.started_at or datetime.now(UTC)
            unit.attempt_count += 1
            attempt = CollectionAttempt(
                unit_id=unit.id,
                step="COLLECT",
                status="RUNNING",
                input_summary={"target_fields": unit.target_fields},
            )
            db.add(attempt)
            refresh_stats(db, task)
            add_event(db, task.id, "task.stats.updated", task.stats)
            db.commit()
            try:
                result = await self.collector.collect(unit.record, unit)
                if self.enforce_quality_contract:
                    validate_production_collection_contract(result)
                db.refresh(task)
                # 慢调用结束后再检查运行版本和停止状态，避免旧运行结果覆盖新一轮任务。
                if task.run_version != unit.run_version or task.status in {
                    TaskStatus.STOPPING,
                    TaskStatus.STOPPED,
                    TaskStatus.DELETED,
                }:
                    unit.status = UnitStatus.DISCARDED
                else:
                    unit.suggestion = result.values
                    unit.validation = result.validation
                    unit.error = None
                    unit.status = UnitStatus.SUCCEEDED
                    apply_resolution(unit)
                    persist_collection_audit(db, unit, result)
                    apply_published_policy(db, unit)
                    unit.finished_at = datetime.now(UTC)
                    for item in result.evidence:
                        db.add(
                            Evidence(
                                unit_id=unit.id,
                                source_url=item.source_url,
                                source_title=item.title,
                                locator=item.locator,
                                excerpt=item.excerpt,
                                metadata_json=item.metadata,
                            )
                        )
                attempt.status = "SUCCEEDED"
                attempt.model = result.model
                attempt.output_summary = {"fields": list(result.values), "validation": result.validation}
                attempt.ended_at = datetime.now(UTC)
            except Exception as exc:  # worker 边界统一记录并隔离提供方、解析和持久化异常
                unit.error = str(exc)
                infrastructure_failure = isinstance(exc, PermissionError)
                source_cooldown = isinstance(exc, SourceCooldownError)
                unit.status = (
                    UnitStatus.FAILED_RETRYABLE
                    if infrastructure_failure or source_cooldown or unit.attempt_count < 3
                    else UnitStatus.FAILED_FINAL
                )
                attempt.status = "FAILED"
                attempt.error = str(exc)
                attempt.ended_at = datetime.now(UTC)
                apply_resolution(unit)
                if unit.status == UnitStatus.FAILED_RETRYABLE:
                    delay = settings.retry_base_seconds * (2 ** max(0, unit.attempt_count - 1))
                    if isinstance(exc, SourceCooldownError):
                        delay = max(delay, exc.retry_after_seconds)
                    unit.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                if infrastructure_failure:
                    task.status = TaskStatus.PAUSED
                    task.version += 1
                    add_event(
                        db,
                        task.id,
                        "task.infrastructure.paused",
                        {
                            "errorType": type(exc).__name__,
                            "message": str(exc),
                            "unitId": unit.id,
                        },
                    )
            # 无论成功或失败都释放租约并在同一事务刷新统计，前端不会长期显示幽灵运行单元。
            unit.lease_owner = None
            unit.leased_until = None
            processed += 1
            refresh_stats(db, task)
            add_event(db, task.id, "task.stats.updated", task.stats)
            db.commit()
            await asyncio.sleep(0)

        db.refresh(task)
        stats = refresh_stats(db, task)
        if stats["pending"] == 0 and stats["running"] == 0:
            task.status = TaskStatus.SUCCEEDED_WITH_ERRORS if stats["failed"] else TaskStatus.SUCCEEDED
            task.version += 1
            add_event(
                db,
                task.id,
                "task.status.changed",
                {"to": task.status, "stats": stats},
            )
        db.commit()


def process_task_sync(task_id: str, collector: Collector | None = None) -> None:
    """为同步 BackgroundTasks/Celery worker 创建独立数据库会话和事件循环。"""

    from .db import SessionLocal

    with SessionLocal() as db:
        asyncio.run(TaskProcessor(collector).process(db, task_id))
