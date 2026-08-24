"""Keep rejected recollections out of completed-review state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_rejected_review_semantics"
down_revision = "0004_review_efficiency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older workers could finish a recollection while leaving the previous
    # REJECTED state on the unit. The ReviewDecision row remains the audit trail.
    op.execute(
        sa.text(
            """
            UPDATE collection_units
            SET review_status = 'UNREVIEWED',
                review_required = true,
                final_values = NULL,
                review_policy_id = NULL,
                review_sampled = false
            WHERE status = 'SUCCEEDED'
              AND review_status = 'REJECTED'
            """
        )
    )


def downgrade() -> None:
    # This is a semantic data repair; the original rejection remains available
    # in review_decisions and must not be reconstructed as current state.
    pass
