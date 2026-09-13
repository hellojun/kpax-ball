"""Persistent cursor for the lending event indexer.

Without this, `_start_block` derived the next block from
`MAX(lending_events.block_number) + 1` and could never advance past empty
windows — a fresh DB rooted at `lending_vault_deploy_block` with several
thousand event-free blocks ahead of it would re-scan the same range
forever (incident with V4, 2026-05-09).

One row per logical indexer loop. Currently only `lending` is in use.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IndexerCursor(Base):
    __tablename__ = "indexer_cursors"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
