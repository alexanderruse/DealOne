"""Real product availability and explicitly separated sandbox checkout."""
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_status VARCHAR(20) NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE permissions ADD COLUMN IF NOT EXISTS checkout_mode VARCHAR(30) NOT NULL DEFAULT 'legacy'")
    op.execute("ALTER TABLE permissions ADD COLUMN IF NOT EXISTS sandbox_total_cents INTEGER")
    # Preserve historical records, but do not let retired fictional watches keep purchasing.
    op.execute("UPDATE permissions SET status='paused' WHERE product_id IN (SELECT id FROM products WHERE source_kind <> 'merchant') AND status IN ('active','draft')")


def downgrade():
    op.drop_column("permissions", "sandbox_total_cents")
    op.drop_column("permissions", "checkout_mode")
    op.drop_column("products", "stock_status")
