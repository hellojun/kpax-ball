"""add lending tables (loans, tos acceptance, events, alerts)

Revision ID: c2d3e4f5a6b7
Revises: b1f947a5789e
Create Date: 2026-04-24 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1f947a5789e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- loans ----------
    op.create_table(
        'loans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('wallet_address', sa.String(length=64), nullable=False),
        sa.Column('ctf_token_id', sa.String(length=80), nullable=False),
        sa.Column('market_slug', sa.String(length=256), nullable=False),
        sa.Column('home_team', sa.String(length=128), nullable=True),
        sa.Column('away_team', sa.String(length=128), nullable=True),
        sa.Column('competition', sa.String(length=128), nullable=True),
        sa.Column('collateral_shares', sa.Numeric(precision=30, scale=6), nullable=False),
        sa.Column('collateral_value_at_open', sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column('principal', sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column('apr_bps', sa.Integer(), nullable=False),
        sa.Column('league_tier', sa.Integer(), nullable=False),
        sa.Column('opened_ltv', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('onchain_loan_id', sa.Integer(), nullable=True),
        sa.Column('open_tx_hash', sa.String(length=80), nullable=True),
        sa.Column('close_tx_hash', sa.String(length=80), nullable=True),
        sa.Column('opened_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('match_kickoff_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('total_interest_paid', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('liquidation_penalty', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('residual_to_user', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('exit_price', sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column('ai_recommended_ltv', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('ai_risk_score', sa.Integer(), nullable=True),
        sa.Column('ai_accepted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_loans_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_loans_user_id'), 'loans', ['user_id'], unique=False)
    op.create_index(op.f('ix_loans_wallet_address'), 'loans', ['wallet_address'], unique=False)
    op.create_index(op.f('ix_loans_ctf_token_id'), 'loans', ['ctf_token_id'], unique=False)
    op.create_index('ix_loans_status_kickoff', 'loans', ['status', 'match_kickoff_at'], unique=False)
    op.create_index('ix_loans_status_ctf_token', 'loans', ['status', 'ctf_token_id'], unique=False)
    op.create_index('ix_loans_user_status', 'loans', ['user_id', 'status'], unique=False)

    # ---------- lending_tos_acceptance ----------
    op.create_table(
        'lending_tos_acceptance',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tos_version', sa.String(length=32), nullable=False),
        sa.Column('signature', sa.String(length=512), nullable=False, server_default='clicked'),
        sa.Column('accepted_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_lending_tos_user'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'tos_version', name='uq_tos_user_version'),
    )
    op.create_index(
        op.f('ix_lending_tos_acceptance_user_id'),
        'lending_tos_acceptance',
        ['user_id'],
        unique=False,
    )

    # ---------- lending_events ----------
    op.create_table(
        'lending_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('block_number', sa.BigInteger(), nullable=False),
        sa.Column('tx_hash', sa.String(length=80), nullable=False),
        sa.Column('log_index', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('indexed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], name='fk_lending_events_loan'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tx_hash', 'log_index', name='uq_lending_events_log'),
    )
    op.create_index(
        op.f('ix_lending_events_loan_id'),
        'lending_events',
        ['loan_id'],
        unique=False,
    )

    # ---------- lending_alerts ----------
    op.create_table(
        'lending_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('alert_type', sa.String(length=32), nullable=False),
        sa.Column('sent_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], name='fk_lending_alerts_loan'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loan_id', 'alert_type', name='uq_lending_alerts_loan_type'),
    )
    op.create_index(
        op.f('ix_lending_alerts_loan_id'),
        'lending_alerts',
        ['loan_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_lending_alerts_loan_id'), table_name='lending_alerts')
    op.drop_table('lending_alerts')

    op.drop_index(op.f('ix_lending_events_loan_id'), table_name='lending_events')
    op.drop_table('lending_events')

    op.drop_index(
        op.f('ix_lending_tos_acceptance_user_id'),
        table_name='lending_tos_acceptance',
    )
    op.drop_table('lending_tos_acceptance')

    op.drop_index('ix_loans_user_status', table_name='loans')
    op.drop_index('ix_loans_status_ctf_token', table_name='loans')
    op.drop_index('ix_loans_status_kickoff', table_name='loans')
    op.drop_index(op.f('ix_loans_ctf_token_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_wallet_address'), table_name='loans')
    op.drop_index(op.f('ix_loans_user_id'), table_name='loans')
    op.drop_table('loans')
