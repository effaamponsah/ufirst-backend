from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# ---------------------------------------------------------------------------
# Enums  (native_enum=False → stored as VARCHAR with CHECK constraint;
#         avoids the complexity of managing Postgres TYPE objects per schema)
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    SPONSOR = "sponsor"
    BENEFICIARY = "beneficiary"
    VENDOR_ADMIN = "vendor_admin"
    VENDOR_CASHIER = "vendor_cashier"
    OPS_AGENT = "ops_agent"
    COMPLIANCE_OFFICER = "compliance_officer"
    ADMIN = "admin"


class KYCStatus(str, enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class KYCSubmissionStatus(str, enum.Enum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class LinkStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    # id comes from Supabase auth.users.id — never auto-generated here
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=30), nullable=False
    )
    kyc_status: Mapped[KYCStatus] = mapped_column(
        Enum(KYCStatus, native_enum=False, length=20),
        nullable=False,
        default=KYCStatus.PENDING,
        server_default=KYCStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    kyc_submissions: Mapped[list[KYCSubmission]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class KYCSubmission(Base):
    __tablename__ = "kyc_submissions"
    __table_args__ = {"schema": "identity"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[KYCSubmissionStatus] = mapped_column(
        Enum(KYCSubmissionStatus, native_enum=False, length=20),
        nullable=False,
        default=KYCSubmissionStatus.PENDING,
    )
    # Reference to the document in object storage (e.g. Supabase Storage path)
    document_refs: Mapped[str | None] = mapped_column(String(2048))
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    reviewer_notes: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="kyc_submissions")


class SponsorBeneficiaryLink(Base):
    __tablename__ = "sponsor_beneficiary_links"
    __table_args__ = (
        UniqueConstraint("sponsor_id", "beneficiary_id", name="uq_sponsor_beneficiary"),
        {"schema": "identity"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    sponsor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    beneficiary_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[LinkStatus] = mapped_column(
        Enum(LinkStatus, native_enum=False, length=20),
        nullable=False,
        default=LinkStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
