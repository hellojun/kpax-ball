"""add delivered_at to lending_alerts + liquidating_at to loans

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-04-29 12:00:00.000000

Plan v0.2 §3 Day 3 + §5 stuck-liquidating reaper:
  - lending_alerts.delivered_at: pull/ack queue marker for SW notifications
  - loans.liquidating_at: timestamp set by keeper when liquidate tx is sent;
    the worker reaper rolls status back to 'active' if this stays > 15 min
    (i.e. tx lost / reorg / RPC outage) so the next tick can retry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'lending_alerts',
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'loans',
        sa.Column('liquidating_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('loans', 'liquidating_at')
    op.drop_column('lending_alerts', 'delivered_at')
