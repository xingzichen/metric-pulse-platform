"""Add versioned review policies, safe bulk previews, and unresolved reports."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_review_efficiency"
down_revision = "0003_collection_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    unit_columns = {column["name"] for column in inspector.get_columns("collection_units")}
    decision_columns = {column["name"] for column in inspector.get_columns("review_decisions")}
    export_columns = {column["name"] for column in inspector.get_columns("export_jobs")}
    if {"review_policies", "review_batches"}.issubset(existing_tables) and {
        "review_policy_id",
        "review_sampled",
    }.issubset(unit_columns) and {"policy_id", "metadata_json"}.issubset(
        decision_columns
    ) and "unresolved_object_key" in export_columns:
        return
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.create_table(
        "review_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("sample_rate", sa.Float(), nullable=False),
        sa.Column("max_sample_error_rate", sa.Float(), nullable=False),
        sa.Column("sample_total", sa.Integer(), nullable=False),
        sa.Column("sample_errors", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_policies_status", "review_policies", ["status"])
    op.create_index(
        "ix_review_policy_name_version", "review_policies", ["name", "version"], unique=True
    )
    if sqlite:
        with op.batch_alter_table("collection_units") as batch:
            batch.add_column(sa.Column("review_policy_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_collection_units_review_policy",
                "review_policies",
                ["review_policy_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "collection_units",
            sa.Column(
                "review_policy_id",
                sa.String(36),
                sa.ForeignKey("review_policies.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    op.add_column(
        "collection_units",
        sa.Column("review_sampled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_collection_units_review_policy_id", "collection_units", ["review_policy_id"])
    if sqlite:
        with op.batch_alter_table("review_decisions") as batch:
            batch.add_column(sa.Column("policy_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_review_decisions_policy",
                "review_policies",
                ["policy_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "review_decisions",
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("review_policies.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    op.add_column(
        "review_decisions",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("export_jobs", sa.Column("unresolved_object_key", sa.String(800), nullable=True))
    op.create_table(
        "review_batches",
        sa.Column("token", sa.String(80), primary_key=True),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("unit_versions", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_batches_task_id", "review_batches", ["task_id"])
    op.create_index("ix_review_batches_expires_at", "review_batches", ["expires_at"])


def downgrade() -> None:
    op.drop_table("review_batches")
    op.drop_column("export_jobs", "unresolved_object_key")
    op.drop_column("review_decisions", "metadata_json")
    op.drop_column("review_decisions", "policy_id")
    op.drop_index("ix_collection_units_review_policy_id", table_name="collection_units")
    op.drop_column("collection_units", "review_sampled")
    op.drop_column("collection_units", "review_policy_id")
    op.drop_table("review_policies")
