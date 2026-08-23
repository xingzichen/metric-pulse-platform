from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FileRecord, TemplateVersion, User
from .task_service import audit


def create_template_from_file(
    db: Session,
    *,
    file: FileRecord,
    name: str,
    actor: User,
) -> TemplateVersion:
    if not file.analysis:
        raise ValueError("File has no analysis")
    latest = db.scalar(select(func.max(TemplateVersion.version)).where(TemplateVersion.name == name))
    schema = {
        "structure_hash": file.analysis["structure_hash"],
        "sheets": [
            {
                key: sheet[key]
                for key in (
                    "name",
                    "header_row",
                    "headers",
                    "descriptor_fields",
                    "target_fields",
                    "business_key_fields",
                    "mode",
                )
            }
            for sheet in file.analysis["sheets"]
        ],
    }
    version = TemplateVersion(
        name=name,
        version=(latest or 0) + 1,
        status="DRAFT",
        schema=schema,
        structure_hash=hashlib.sha256(
            json.dumps(schema, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        created_by=actor.id,
    )
    db.add(version)
    db.flush()
    audit(
        db,
        actor_id=actor.id,
        action="template.create",
        resource_type="template_version",
        resource_id=version.id,
        after={"name": name, "version": version.version},
    )
    db.commit()
    return version


def publish_template(db: Session, template: TemplateVersion, actor: User) -> None:
    template.status = "PUBLISHED"
    audit(
        db,
        actor_id=actor.id,
        action="template.publish",
        resource_type="template_version",
        resource_id=template.id,
        after={"status": "PUBLISHED"},
    )
    db.commit()
