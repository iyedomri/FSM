"""Retry legacy failed-document vector cleanup once under explicit pending state."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE documents SET status = 'cleanup_pending' WHERE status = 'failed'")


def downgrade():
    op.execute("UPDATE documents SET status = 'failed' WHERE status = 'cleanup_pending'")
