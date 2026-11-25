"""Persistent account data; tokens and verification codes belong in Redis."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from formaai.infrastructure.database import Base, UTCDateTime


class UserAccount(Base):
    """Account identity retained after user-requested logical deletion."""

    __tablename__ = "user_account"
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_account_email"),
        CheckConstraint("role IN ('user', 'admin')", name="ck_user_account_role"),
        CheckConstraint(
            "status IN ('active', 'disabling', 'disabled', 'deleted')",
            name="ck_user_account_status",
        ),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="ck_user_account_deleted_at",
        ),
        CheckConstraint("version >= 0", name="ck_user_account_version"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(
        String(16), server_default=text("'user'"), default="user"
    )
    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'active'"), default="active"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )
