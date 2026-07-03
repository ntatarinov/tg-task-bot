from __future__ import annotations

"""Add reporter_text to tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("reporter_text", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "reporter_text")
