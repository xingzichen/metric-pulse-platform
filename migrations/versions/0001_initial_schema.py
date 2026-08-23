"""Create the initial Metric Pulse schema."""

from alembic import op

from metric_pulse import models  # noqa: F401
from metric_pulse.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
