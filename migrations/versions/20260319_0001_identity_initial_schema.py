"""identity: initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column(
            "role",
            sa.String(30),
            nullable=False,
        ),
        sa.Column(
            "kyc_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema="identity",
    )

    op.create_table(
        "kyc_submissions",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("document_refs", sa.String(2048), nullable=True),
        sa.Column("provider_ref", sa.String(255), nullable=True),
        sa.Column("reviewer_notes", sa.String(2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name="fk_kyc_submissions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kyc_submissions"),
        schema="identity",
    )

    op.create_table(
        "sponsor_beneficiary_links",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sponsor_id", sa.UUID(), nullable=False),
        sa.Column("beneficiary_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sponsor_id"],
            ["identity.users.id"],
            name="fk_sponsor_beneficiary_links_sponsor_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["beneficiary_id"],
            ["identity.users.id"],
            name="fk_sponsor_beneficiary_links_beneficiary_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sponsor_beneficiary_links"),
        sa.UniqueConstraint(
            "sponsor_id", "beneficiary_id", name="uq_sponsor_beneficiary"
        ),
        schema="identity",
    )

    # Indexes for common query patterns
    op.create_index(
        "ix_kyc_submissions_user_id",
        "kyc_submissions",
        ["user_id"],
        schema="identity",
    )
    op.create_index(
        "ix_sponsor_beneficiary_links_sponsor_id",
        "sponsor_beneficiary_links",
        ["sponsor_id"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_table("sponsor_beneficiary_links", schema="identity")
    op.drop_table("kyc_submissions", schema="identity")
    op.drop_table("users", schema="identity")
