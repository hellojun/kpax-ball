"""add users table for privy auth

Revision ID: b1f947a5789e
Revises: b8b17c3035b0
Create Date: 2026-04-22 11:20:09.038674
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1f947a5789e'
down_revision: Union[str, None] = 'b8b17c3035b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('privy_user_id', sa.String(length=128), nullable=False),
        sa.Column('wallet_address', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=256), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'last_login_at',
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('privy_user_id'),
    )
    op.create_index(
        op.f('ix_users_privy_user_id'), 'users', ['privy_user_id'], unique=True
    )
    op.create_index(
        op.f('ix_users_wallet_address'), 'users', ['wallet_address'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_users_wallet_address'), table_name='users')
    op.drop_index(op.f('ix_users_privy_user_id'), table_name='users')
    op.drop_table('users')
