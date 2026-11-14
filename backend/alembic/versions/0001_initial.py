"""Initial immutable DealOne schema and TimescaleDB price history."""
from alembic import op
from sqlalchemy import text

revision = "0001"
down_revision = None

SCHEMA_SQL = r"""
CREATE TABLE heartbeats (
	id VARCHAR(30) NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE notifications (
	id VARCHAR(36) NOT NULL,
	owner VARCHAR(36) NOT NULL,
	event_key VARCHAR(100) NOT NULL,
	message TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner, event_key)
);

CREATE INDEX ix_notifications_owner ON notifications (owner);

CREATE TABLE price_observations (
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	product_id VARCHAR(64) NOT NULL,
	price_cents INTEGER NOT NULL,
	total_cents INTEGER NOT NULL,
	PRIMARY KEY (observed_at, id)
);

CREATE INDEX ix_price_observations_product_id ON price_observations (product_id);

CREATE TABLE products (
	id VARCHAR(64) NOT NULL,
	name VARCHAR(180) NOT NULL,
	variant VARCHAR(100) NOT NULL,
	seller VARCHAR(100) NOT NULL,
	condition VARCHAR(30) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	price_cents INTEGER NOT NULL,
	original_cents INTEGER NOT NULL,
	shipping_cents INTEGER NOT NULL,
	tax_basis_points INTEGER NOT NULL,
	stock INTEGER NOT NULL,
	category VARCHAR(50) NOT NULL,
	color VARCHAR(30) NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE permissions (
	id VARCHAR(36) NOT NULL,
	owner VARCHAR(36) NOT NULL,
	product_id VARCHAR(64) NOT NULL,
	variant VARCHAR(100) NOT NULL,
	seller VARCHAR(100) NOT NULL,
	condition VARCHAR(30) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	quantity INTEGER NOT NULL,
	max_total_cents INTEGER NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(30) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(product_id) REFERENCES products (id)
);

CREATE INDEX ix_permissions_owner ON permissions (owner);

CREATE INDEX ix_permissions_status ON permissions (status);

CREATE TABLE orders (
	id VARCHAR(36) NOT NULL,
	permission_id VARCHAR(36) NOT NULL,
	owner VARCHAR(36) NOT NULL,
	status VARCHAR(30) NOT NULL,
	total_cents INTEGER NOT NULL,
	confirmation VARCHAR(80),
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (permission_id),
	FOREIGN KEY(permission_id) REFERENCES permissions (id)
);

CREATE INDEX ix_orders_owner ON orders (owner);

CREATE INDEX ix_orders_status ON orders (status);

CREATE TABLE merchant_orders (
	order_id VARCHAR(36) NOT NULL,
	confirmation VARCHAR(80) NOT NULL,
	total_cents INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (order_id),
	FOREIGN KEY(order_id) REFERENCES orders (id),
	UNIQUE (confirmation)
);

CREATE TABLE payment_authorizations (
	order_id VARCHAR(36) NOT NULL,
	token VARCHAR(80) NOT NULL,
	merchant VARCHAR(100) NOT NULL,
	amount_cents INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	PRIMARY KEY (order_id),
	FOREIGN KEY(order_id) REFERENCES orders (id),
	UNIQUE (token)
);
"""

def upgrade():
    bind = op.get_bind()
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)
    available = bind.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='timescaledb')"))
    if available:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute("SELECT create_hypertable('price_observations', 'observed_at', if_not_exists => TRUE)")
    else:
        from app.config import settings
        if settings().app_env == "production":
            raise RuntimeError("Tiger Cloud / TimescaleDB is required in production")


def downgrade():
    op.drop_table('payment_authorizations')
    op.drop_table('merchant_orders')
    op.drop_table('orders')
    op.drop_table('permissions')
    op.drop_table('products')
    op.drop_table('price_observations')
    op.drop_table('notifications')
    op.drop_table('heartbeats')
