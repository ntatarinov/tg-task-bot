from __future__ import annotations

"""Add question_message_id to clarifications

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clarifications",
        sa.Column("question_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clarifications", "question_message_id")
