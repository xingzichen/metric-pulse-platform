"""ORM 对象到前端 JSON 视图的稳定映射。

集中处理 snake_case 到 camelCase、枚举字符串化和详情字段展开，避免不同路由对同一资源
返回不同语义。视图函数不提交事务，也不触发业务操作。
"""

from __future__ import annotations

from typing import Any

from .models import CollectionTask, CollectionUnit, FileRecord, WorkbookSheet
from .state_machine import allowed_actions


def file_view(file: FileRecord, *, include_analysis: bool = False) -> dict[str, Any]:
    """序列化文件摘要；体积较大的分析 JSON 只在详情接口返回。"""

    result = {
        "id": file.id,
        "originalName": file.original_name,
        "contentHash": file.content_hash,
        "size": file.size,
        "status": file.status,
        "error": file.error,
        "createdAt": file.created_at,
        "updatedAt": file.updated_at,
    }
    if include_analysis:
        result["analysis"] = file.analysis
    return result


def sheet_view(sheet: WorkbookSheet) -> dict[str, Any]:
    """序列化工作表字段角色和置信度。"""

    return {
        "id": sheet.id,
        "name": sheet.name,
        "position": sheet.position,
        "headerRow": sheet.header_row,
        "dataStartRow": sheet.data_start_row,
        "maxRow": sheet.max_row,
        "maxColumn": sheet.max_column,
        "headers": sheet.headers,
        "displayHeaders": sheet.display_headers,
        "descriptorFields": sheet.descriptor_fields,
        "targetFields": sheet.target_fields,
        "businessKeyFields": sheet.business_key_fields,
        "confidence": sheet.confidence,
        "needsConfirmation": sheet.needs_confirmation,
        "profile": sheet.profile,
    }


def task_view(task: CollectionTask, *, detail: bool = False) -> dict[str, Any]:
    """序列化任务统计、运行版本、控制版本及当前可用操作。"""

    result = {
        "id": task.id,
        "name": task.name,
        "fileId": task.file_id,
        "status": task.status,
        "version": task.version,
        "runVersion": task.run_version,
        "stats": task.stats,
        "allowedActions": allowed_actions(task.status),
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
    }
    if detail:
        result["config"] = task.config
    return result


def unit_view(unit: CollectionUnit, *, detail: bool = False) -> dict[str, Any]:
    """序列化执行/解决/审核三套状态；详情额外展开源行与行契约。"""

    result = {
        "id": unit.id,
        "taskId": unit.task_id,
        "recordId": unit.record_id,
        "status": unit.status,
        "executionStatus": unit.status,
        "resolutionStatus": unit.resolution_status,
        "resolutionReason": unit.resolution_reason,
        "reviewStatus": unit.review_status,
        "reviewRequired": unit.review_required,
        "riskLevel": unit.risk_level,
        "validationVersion": unit.validation_version,
        "targetFields": unit.target_fields,
        "suggestion": unit.suggestion,
        "finalValues": unit.final_values,
        "validation": unit.validation,
        "error": unit.error,
        "version": unit.version,
    }
    if detail:
        result["record"] = {
            "sheetName": unit.record.sheet_name,
            "sourceRow": unit.record.source_row,
            "businessKey": unit.record.business_key,
            "rawData": unit.record.raw_data,
            "rowContract": unit.record.row_contract,
        }
    return result
