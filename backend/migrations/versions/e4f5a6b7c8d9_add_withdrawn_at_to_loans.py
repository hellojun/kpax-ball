"""add withdrawn_at to loans (Sprint 4 — V2 4-step liquidation)

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-30 13:00:00.000000

V2 introduces the `withdrawing` Loan status (active → withdrawing →
liquidating → liquidated_*). `withdrawn_at` records when the keeper called
`withdrawCtfForLiquidation`. Reaper alerts admin if a loan stays in
`withdrawing` longer than 30 min — typically a stuck Polymarket sell that
needs intervention.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'loans',
        sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('loans', 'withdrawn_at')
