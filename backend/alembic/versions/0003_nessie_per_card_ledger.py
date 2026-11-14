"""Per-permission Nessie accounts, bank charge mapping, and settlement status."""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute(
        "ALTER TABLE virtual_cards ADD COLUMN IF NOT EXISTS nessie_account_id VARCHAR(64)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_charges (
            order_id VARCHAR(36) NOT NULL,
            permission_id VARCHAR(36) NOT NULL,
            account_id VARCHAR(64) NOT NULL,
            transaction_id VARCHAR(80) NOT NULL,
            amount_cents INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (order_id),
            FOREIGN KEY(order_id) REFERENCES orders (id),
            FOREIGN KEY(permission_id) REFERENCES permissions (id),
            UNIQUE (transaction_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bank_charges_permission ON bank_charges (permission_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bank_charges_status ON bank_charges (status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS bank_charges")
    op.execute("ALTER TABLE virtual_cards DROP COLUMN IF EXISTS nessie_account_id")
