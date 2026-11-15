"""Merchant provenance and product artwork. Existing permissions remain unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0002_merchant"
down_revision = "0001"


def upgrade():
    op.add_column("products", sa.Column("source_kind", sa.String(20), nullable=False, server_default="demo"))
    op.add_column("products", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("image_url", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("icon", sa.String(30), nullable=False, server_default="package"))
    op.add_column("products", sa.Column("price_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for name in ("price_checked_at", "icon", "image_url", "source_url", "source_kind"):
        op.drop_column("products", name)
