"""widen prompt_version

Revision ID: c1f4b2d7e803
Revises: 9a5a496c990f
Create Date: 2026-08-18 16:20:11.004512
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f4b2d7e803"
down_revision: str | None = "9a5a496c990f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Give prompt identifiers room to be descriptive.

    ``VARCHAR(32)`` fitted the first prompt ("answer-gen/2026-08-17", 21
    characters) with nothing to spare, and the requirement-extraction prompt
    ran past it at 33. The failure mode is what makes this worth a migration:
    the string is only written at INSERT time, at the end of a generation that
    has already cost a model call, so a renamed prompt takes down the write
    path in production rather than failing at startup or in review.
    """
    op.alter_column(
        "generated_proposals",
        "prompt_version",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Truncate first: narrowing the column outright fails on any row already
    # holding a longer identifier, which would leave the rollback stuck
    # halfway with no way forward or back.
    op.execute(
        sa.text(
            "UPDATE generated_proposals SET prompt_version = left(prompt_version, 32) "
            "WHERE length(prompt_version) > 32"
        )
    )
    op.alter_column(
        "generated_proposals",
        "prompt_version",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=True,
    )
