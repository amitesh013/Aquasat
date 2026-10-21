"""Add unique usernames and fill usernames for existing accounts."""

import re

from alembic import op
import sqlalchemy as sa


revision = "0003_usernames"
down_revision = "0002_user_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_users", sa.Column("username", sa.String(length=30), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, email FROM auth_users ORDER BY id")
    ).mappings().all()
    used_usernames: set[str] = set()
    for row in rows:
        local_part = str(row["email"]).split("@", 1)[0].casefold()
        base = re.sub(r"[^a-z0-9._-]+", "_", local_part).strip("._-")
        base = base[:30].rstrip("._-")
        if len(base) < 3:
            base = f"{base or 'user'}_acct"
        candidate = base
        suffix = 2
        while candidate in used_usernames:
            suffix_text = f"_{suffix}"
            candidate = f"{base[:30 - len(suffix_text)].rstrip('._-')}{suffix_text}"
            suffix += 1
        used_usernames.add(candidate)
        connection.execute(
            sa.text("UPDATE auth_users SET username = :username WHERE id = :user_id"),
            {"username": candidate, "user_id": row["id"]},
        )

    op.alter_column(
        "auth_users", "username", existing_type=sa.String(length=30), nullable=False
    )
    op.create_unique_constraint("uq_auth_users_username", "auth_users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_auth_users_username", "auth_users", type_="unique")
    op.drop_column("auth_users", "username")
