"""Add independent execution, resolution, and review semantics."""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0002_resolution"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def upgrade() -> None:
    # 0001 historically used current ORM metadata. A brand-new database therefore already contains
    # later columns; keep the historical migration usable for old databases while making fresh upgrade
    # idempotent until 0001 is replaced by a frozen baseline.
    if "resolution_status" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("collection_units")
    }:
        return
    op.add_column(
        "collection_units",
        sa.Column("resolution_status", sa.String(40), nullable=False, server_default="NOT_EVALUATED"),
    )
    op.add_column("collection_units", sa.Column("resolution_reason", sa.String(120), nullable=True))
    op.add_column(
        "collection_units",
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "collection_units",
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="HIGH"),
    )
    op.add_column(
        "collection_units",
        sa.Column("validation_version", sa.String(40), nullable=False, server_default="resolution-v1"),
    )
    op.create_index("ix_collection_units_resolution_status", "collection_units", ["resolution_status"])
    op.create_index("ix_collection_units_review_required", "collection_units", ["review_required"])
    op.create_index("ix_collection_units_risk_level", "collection_units", ["risk_level"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, status, target_fields, suggestion, validation "
            "FROM collection_units WHERE status = 'SUCCEEDED'"
        )
    ).mappings()
    for row in rows:
        validation = _json(row["validation"])
        values = _json(row["suggestion"])
        targets = _json(row["target_fields"])
        if not isinstance(validation, dict) or not isinstance(values, dict) or not isinstance(targets, list):
            continue
        value_targets = [field for field in targets if field not in {"source", "source_url"}] or targets
        present = [field for field in value_targets if values.get(field) not in (None, "")]
        if validation.get("valid") is True and len(present) == len(value_targets):
            status, reason, risk = "RESOLVED", "MIGRATED_VALID_COMPLETE", "MEDIUM"
        elif present:
            status, reason, risk = "PARTIAL", "MIGRATED_PARTIAL", "HIGH"
        else:
            status, reason, risk = "UNRESOLVED", "MIGRATED_EMPTY_RESULT", "HIGH"
        connection.execute(
            sa.text(
                "UPDATE collection_units SET resolution_status=:status, resolution_reason=:reason, "
                "risk_level=:risk WHERE id=:id"
            ),
            {"status": status, "reason": reason, "risk": risk, "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_index("ix_collection_units_risk_level", table_name="collection_units")
    op.drop_index("ix_collection_units_review_required", table_name="collection_units")
    op.drop_index("ix_collection_units_resolution_status", table_name="collection_units")
    op.drop_column("collection_units", "validation_version")
    op.drop_column("collection_units", "risk_level")
    op.drop_column("collection_units", "review_required")
    op.drop_column("collection_units", "resolution_reason")
    op.drop_column("collection_units", "resolution_status")
