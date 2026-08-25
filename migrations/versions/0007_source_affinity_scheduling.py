"""Add normalized-source affinity scheduling key."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_source_affinity_scheduling"
down_revision = "0006_direct_source_acquisition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("collection_units")}
    if "source_affinity_key" not in columns:
        op.add_column(
            "collection_units",
            sa.Column("source_affinity_key", sa.String(64), nullable=True),
        )
        op.create_index(
            "ix_collection_units_source_affinity_key",
            "collection_units",
            ["source_affinity_key"],
        )


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("collection_units")}
    if "source_affinity_key" in columns:
        op.drop_index("ix_collection_units_source_affinity_key", table_name="collection_units")
        op.drop_column("collection_units", "source_affinity_key")
