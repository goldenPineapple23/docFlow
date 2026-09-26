"""
Create the `docflow_app` role on the CI database, exactly as SETUP.md Step 1.6
creates it on docflow-staging: LOGIN, NOBYPASSRLS, no CREATE on `public`,
and full DML on every table and sequence.

CI only (Phase 5.5 Stage 0, DECISIONS.md D-148). Run after `supabase start`
has applied supabase/migrations/, so the grants cover every table the
migrations created. Idempotent.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
  APP_ROLE_PASSWORD       the password to give `docflow_app` (CI-local only)
"""

import os
import sys

import psycopg
from psycopg import sql


def main() -> int:
    superuser_url = os.environ["SUPERUSER_DATABASE_URL"]
    password = os.environ["APP_ROLE_PASSWORD"]

    with psycopg.connect(superuser_url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'docflow_app'"
        ).fetchone()
        if exists is None:
            conn.execute(
                sql.SQL(
                    "CREATE ROLE docflow_app WITH LOGIN PASSWORD {} NOBYPASSRLS"
                ).format(sql.Literal(password))
            )
        for statement in (
            "GRANT USAGE ON SCHEMA public TO docflow_app",
            "GRANT ALL ON ALL TABLES IN SCHEMA public TO docflow_app",
            "GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO docflow_app",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO docflow_app",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO docflow_app",
        ):
            conn.execute(statement)

        row = conn.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'docflow_app'"
        ).fetchone()
        if row is None or row[0] or row[1]:
            print("docflow_app must exist without BYPASSRLS or SUPERUSER", file=sys.stderr)
            return 1
        can_create = conn.execute(
            "SELECT has_schema_privilege('docflow_app', 'public', 'CREATE')"
        ).fetchone()
        if can_create is None or can_create[0]:
            print("docflow_app must not have CREATE on schema public", file=sys.stderr)
            return 1

    print("docflow_app ready: LOGIN, NOBYPASSRLS, no CREATE on public")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
