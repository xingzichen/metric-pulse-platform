"""Add row search, model-call, source-snapshot, and lease audit data."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_collection_audit"
down_revision = "0002_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("collection_units", sa.Column("lease_owner", sa.String(120), nullable=True))
    op.add_column("collection_units", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_collection_units_next_attempt_at", "collection_units", ["next_attempt_at"])

    op.create_table(
        "row_search_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(36),
            sa.ForeignKey("collection_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_row_search_attempts_unit_id", "row_search_attempts", ["unit_id"])
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(36),
            sa.ForeignKey("collection_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_model_calls_unit_id", "model_calls", ["unit_id"])
    op.create_index("ix_model_calls_phase", "model_calls", ["phase"])
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_key", sa.String(64), nullable=False),
        sa.Column("normalized_url", sa.String(2000), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_source_snapshots_snapshot_key", "source_snapshots", ["snapshot_key"], unique=True
    )
    op.create_index("ix_source_snapshots_normalized_url", "source_snapshots", ["normalized_url"])
    op.create_index("ix_source_snapshots_content_hash", "source_snapshots", ["content_hash"])
    op.create_table(
        "unit_source_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(36),
            sa.ForeignKey("collection_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("source_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_attempt_id",
            sa.String(36),
            sa.ForeignKey("row_search_attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("locator", sa.String(500), nullable=True),
    )
    op.create_index("ix_unit_source_links_unit_id", "unit_source_links", ["unit_id"])
    op.create_index("ix_unit_source_links_snapshot_id", "unit_source_links", ["snapshot_id"])
    op.create_index(
        "ix_unit_source_links_search_attempt_id", "unit_source_links", ["search_attempt_id"]
    )
    op.create_index(
        "ix_unit_source_link_unique", "unit_source_links", ["unit_id", "snapshot_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("unit_source_links")
    op.drop_table("source_snapshots")
    op.drop_table("model_calls")
    op.drop_table("row_search_attempts")
    op.drop_index("ix_collection_units_next_attempt_at", table_name="collection_units")
    op.drop_column("collection_units", "next_attempt_at")
    op.drop_column("collection_units", "lease_owner")
