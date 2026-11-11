"""Initial schema. Future revisions must use explicit immutable Alembic operations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                    sa.Column("email", sa.String(254), nullable=False, unique=True),
                    sa.Column("name", sa.String(80), nullable=False), sa.Column("password_hash", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("refresh_tokens", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
                    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("used_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)))
    for name in ["user_id", "family_id"]:
        op.create_index("ix_refresh_tokens_" + name, "refresh_tokens", [name])
    op.create_table("documents", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("filename", sa.String(160), nullable=False), sa.Column("source", sa.String(20), nullable=False),
                    sa.Column("source_key", sa.String(64)), sa.Column("status", sa.String(20), nullable=False),
                    sa.Column("payload", pg.JSONB()), sa.Column("pages", pg.JSONB()),
                    sa.Column("chunk_count", sa.Integer(), nullable=False), sa.Column("best_score", sa.Integer(), nullable=False),
                    sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                    sa.UniqueConstraint("user_id", "source_key", name="uq_document_source"))
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_table("schedule_items", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("course", sa.String(100), nullable=False), sa.Column("room", sa.String(20), nullable=False),
                    sa.Column("weekday", sa.Integer(), nullable=False), sa.Column("start_minute", sa.Integer(), nullable=False),
                    sa.Column("end_minute", sa.Integer(), nullable=False),
                    sa.CheckConstraint("weekday BETWEEN 0 AND 6 AND start_minute >= 0 AND end_minute <= 1440 AND end_minute > start_minute", name="ck_schedule_time"))
    op.create_index("ix_schedule_items_user_id", "schedule_items", ["user_id"])
    op.create_table("study_sessions", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("ended_at", sa.DateTime(timezone=True)), sa.Column("seconds", sa.Integer(), nullable=False))
    op.create_index("ix_study_sessions_user_id", "study_sessions", ["user_id"])
    op.create_index("uq_active_study", "study_sessions", ["user_id"], unique=True, postgresql_where=sa.text("ended_at IS NULL"))
    op.create_table("google_connections",
                    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("credentials", sa.Text(), nullable=False), sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("last_sync_at", sa.DateTime(timezone=True)),
                    sa.Column("last_error", sa.String(200)))
    op.create_index("ix_google_connections_next_sync_at", "google_connections", ["next_sync_at"])


def downgrade():
    for table in ["google_connections", "study_sessions", "schedule_items", "documents", "refresh_tokens", "users"]:
        op.drop_table(table)
