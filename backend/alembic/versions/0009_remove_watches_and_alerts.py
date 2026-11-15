"""Support removing watches and dismissing notifications."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"


def upgrade():
    op.add_column("permissions", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column("notifications", sa.Column("dismissed_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("notifications", "dismissed_at")
    op.drop_column("permissions", "removed_at")
