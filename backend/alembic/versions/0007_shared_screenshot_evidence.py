"""Persist checkout evidence across replicas and container restarts."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    op.add_column("checkout_demo_runs", sa.Column("screenshot_png", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("checkout_demo_runs", "screenshot_png")
