"""Merge permission-card and merchant-catalog branches onto a single 0002 head."""

revision = "0002"
down_revision = ("0002_cards", "0002_merchant")


def upgrade():
    pass


def downgrade():
    pass
