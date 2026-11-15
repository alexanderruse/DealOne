"""Shared public-store discovery queue and cache."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def upgrade():
    op.create_table("catalog_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("store", sa.String(30), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("catalog_tasks")
