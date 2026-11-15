"""OpenRouter spend tracking, approved domains whitelist, and checkout evidence.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-15
"""
from alembic import op
from sqlalchemy import text

revision = "0005"
down_revision = "0004"


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS approved_domains (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        domain VARCHAR(120) NOT NULL UNIQUE,
        display_name VARCHAR(100) NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_approved_domains_domain ON approved_domains (domain);

    CREATE TABLE IF NOT EXISTS ai_spend_records (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        purpose VARCHAR(50) NOT NULL,
        model VARCHAR(100) NOT NULL,
        status VARCHAR(20) NOT NULL,
        estimated_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        actual_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        generation_id VARCHAR(120)
    );
    CREATE INDEX IF NOT EXISTS ix_ai_spend_records_created_at ON ai_spend_records (created_at);
    CREATE INDEX IF NOT EXISTS ix_ai_spend_records_status ON ai_spend_records (status);

    CREATE TABLE IF NOT EXISTS checkout_demo_runs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        permission_id VARCHAR(36) NOT NULL REFERENCES permissions (id),
        owner VARCHAR(36) NOT NULL,
        product_id VARCHAR(64) NOT NULL REFERENCES products (id),
        trigger_type VARCHAR(30) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'queued',
        blocker_reason TEXT,
        nessie_verified_limit_cents INTEGER NOT NULL DEFAULT 0,
        fields_completed TEXT NOT NULL DEFAULT '[]',
        screenshot_path TEXT,
        is_submitted BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_checkout_demo_runs_permission_id ON checkout_demo_runs (permission_id);
    CREATE INDEX IF NOT EXISTS ix_checkout_demo_runs_owner ON checkout_demo_runs (owner);
    CREATE INDEX IF NOT EXISTS ix_checkout_demo_runs_status ON checkout_demo_runs (status);

    INSERT INTO approved_domains (id, domain, display_name, created_at)
    VALUES ('seed-keychron', 'www.keychron.com', 'Keychron Store', NOW())
    ON CONFLICT (domain) DO NOTHING;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS checkout_demo_runs;
    DROP TABLE IF EXISTS ai_spend_records;
    DROP TABLE IF EXISTS approved_domains;
    """)
