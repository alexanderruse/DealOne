"""Per-permission virtual cards, ask-first mode, and product descriptions."""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE permissions ADD COLUMN IF NOT EXISTS purchase_mode VARCHAR(20) NOT NULL DEFAULT 'ask_first'"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS virtual_cards (
            permission_id VARCHAR(36) NOT NULL,
            last4 VARCHAR(4) NOT NULL,
            limit_cents INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (permission_id),
            FOREIGN KEY(permission_id) REFERENCES permissions (id)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS virtual_cards")
    op.execute("ALTER TABLE permissions DROP COLUMN IF EXISTS purchase_mode")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS description")
