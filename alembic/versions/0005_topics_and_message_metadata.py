from __future__ import annotations

"""Topics, message metadata, digest kinds and message uniqueness

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

from app.db.schema import upgrade_schema

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    upgrade_schema(op.get_bind())


def downgrade() -> None:
    for table in ("topic_messages", "topic_item_sources", "topic_items", "topic_updates", "topics"):
        op.drop_table(table)
    op.drop_index("uq_digests_slot", table_name="digests")
    op.drop_index("ix_messages_chat_sent_at", table_name="messages")
    op.drop_index("ix_messages_chat_pending", table_name="messages")
    op.drop_index("uq_messages_chat_telegram_id", table_name="messages")
    for column in ("sent_chunks", "period_key", "kind"):
        op.drop_column("digests", column)
    for column in (
        "source", "forward_from", "content_type", "edited_at", "sent_at",
        "analysis_failed", "analysis_attempts",
    ):
        op.drop_column("messages", column)
    for column in ("digest_chat_id", "weekly_digest_day", "weekly_digest_enabled"):
        op.drop_column("chats", column)
