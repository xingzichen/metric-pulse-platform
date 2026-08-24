"""Add direct-source acquisition route audit."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_direct_source_acquisition"
down_revision = "0005_rejected_review_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_acquisition_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(36),
            sa.ForeignKey("collection_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_attempt_id",
            sa.String(36),
            sa.ForeignKey("row_search_attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("route", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(80), nullable=True),
        sa.Column("input_url", sa.String(2000), nullable=True),
        sa.Column("normalized_url", sa.String(2000), nullable=True),
        sa.Column("final_url", sa.String(2000), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("persistent_cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("match_status", sa.String(80), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_source_acquisition_attempts_unit_id",
        "source_acquisition_attempts",
        ["unit_id"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_search_attempt_id",
        "source_acquisition_attempts",
        ["search_attempt_id"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_route",
        "source_acquisition_attempts",
        ["route"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_status",
        "source_acquisition_attempts",
        ["status"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_reason",
        "source_acquisition_attempts",
        ["reason"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_normalized_url",
        "source_acquisition_attempts",
        ["normalized_url"],
    )
    op.create_index(
        "ix_source_acquisition_attempts_content_hash",
        "source_acquisition_attempts",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_table("source_acquisition_attempts")
