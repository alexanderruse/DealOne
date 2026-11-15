"""Record the verified checkout demo cart amount."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"


def upgrade():
    op.add_column("checkout_demo_runs", sa.Column("cart_total_cents", sa.Integer()))
    op.add_column("checkout_demo_runs", sa.Column("cart_discount_cents", sa.Integer()))


def downgrade():
    op.drop_column("checkout_demo_runs", "cart_discount_cents")
    op.drop_column("checkout_demo_runs", "cart_total_cents")
