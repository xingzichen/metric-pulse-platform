from __future__ import annotations

from typing import Any

from .models import CollectionTask, CollectionUnit, FileRecord, WorkbookSheet
from .state_machine import allowed_actions


def file_view(file: FileRecord, *, include_analysis: bool = False) -> dict[str, Any]:
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
    result = {
        "id": unit.id,
        "taskId": unit.task_id,
        "recordId": unit.record_id,
        "status": unit.status,
        "reviewStatus": unit.review_status,
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
