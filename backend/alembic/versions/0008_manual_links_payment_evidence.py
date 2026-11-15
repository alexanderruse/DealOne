"""Support full product links and a second checkout evidence image."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade():
    op.alter_column("catalog_tasks", "target", type_=sa.String(500), existing_type=sa.String(200))
    op.add_column("checkout_demo_runs", sa.Column("payment_screenshot_png", sa.LargeBinary(), nullable=True))


def downgrade():
    op.execute("DELETE FROM catalog_tasks WHERE kind = 'link'")
    op.alter_column("catalog_tasks", "target", type_=sa.String(200), existing_type=sa.String(500))
    op.drop_column("checkout_demo_runs", "payment_screenshot_png")
