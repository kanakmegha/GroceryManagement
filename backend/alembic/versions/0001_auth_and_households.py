"""auth and households

Revision ID: 0001_auth_and_households
Revises:
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_auth_and_households"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("clerk_user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
    )

    op.create_table(
        "households",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_households_created_by_users", ondelete="RESTRICT"
        ),
    )

    op.create_table(
        "household_members",
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'owner'"),
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name="fk_household_members_household_id_households",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_household_members_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "user_id", name="pk_household_members"),
    )
    op.create_index("idx_household_members_user_id", "household_members", ["user_id"])

    if is_postgres:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION upsert_clerk_user(
                p_clerk_user_id TEXT,
                p_email TEXT,
                p_display_name TEXT
            )
            RETURNS UUID
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                v_user_id UUID;
            BEGIN
                INSERT INTO users (id, clerk_user_id, email, display_name, created_at, updated_at)
                VALUES (
                    gen_random_uuid(),
                    p_clerk_user_id,
                    p_email,
                    COALESCE(NULLIF(trim(p_display_name), ''), 'User'),
                    now(),
                    now()
                )
                ON CONFLICT (clerk_user_id) DO UPDATE
                SET email = COALESCE(EXCLUDED.email, users.email),
                    updated_at = now()
                RETURNING id INTO v_user_id;

                RETURN v_user_id;
            END;
            $$;
            """
        )

        op.execute("ALTER TABLE household_members ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE household_members FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY household_members_isolation ON household_members
                USING (
                    user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    OR household_id = NULLIF(
                        current_setting('app.current_household_id', true), ''
                    )::uuid
                )
                WITH CHECK (
                    user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    OR household_id = NULLIF(
                        current_setting('app.current_household_id', true), ''
                    )::uuid
                );
            """
        )

        op.execute("ALTER TABLE households ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE households FORCE ROW LEVEL SECURITY;")
        op.execute(
            """
            CREATE POLICY households_isolation ON households
                USING (
                    created_by = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    OR id IN (
                        SELECT household_id
                        FROM household_members
                        WHERE user_id = NULLIF(
                            current_setting('app.current_user_id', true), ''
                        )::uuid
                    )
                )
                WITH CHECK (
                    created_by = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                );
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS households_isolation ON households;")
        op.execute("ALTER TABLE households NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE households DISABLE ROW LEVEL SECURITY;")
        op.execute("DROP POLICY IF EXISTS household_members_isolation ON household_members;")
        op.execute("ALTER TABLE household_members NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE household_members DISABLE ROW LEVEL SECURITY;")
        op.execute("DROP FUNCTION IF EXISTS upsert_clerk_user(TEXT, TEXT, TEXT);")

    op.drop_index("idx_household_members_user_id", table_name="household_members")
    op.drop_table("household_members")
    op.drop_table("households")
    op.drop_table("users")
