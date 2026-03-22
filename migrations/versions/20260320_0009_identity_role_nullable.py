"""identity: make role column nullable for JWT-less onboarding

New users from Supabase JWTs may not carry a role in app_metadata.
The user picks their role during POST /onboarding/complete-profile.
Role is now nullable — NULL means "not yet onboarded".

Revision ID: 0009
Revises: 20260319_0008
Create Date: 2026-03-20
"""

from alembic import op

revision = "0009"
down_revision = "20260319_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "role",
        nullable=True,
        schema="identity",
    )


def downgrade() -> None:
    # Set any NULLs to 'sponsor' before making NOT NULL again
    op.execute("UPDATE identity.users SET role = 'sponsor' WHERE role IS NULL")
    op.alter_column(
        "users",
        "role",
        nullable=False,
        schema="identity",
    )
