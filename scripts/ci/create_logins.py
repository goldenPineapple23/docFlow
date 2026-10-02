"""
Turn on the four database logins on the CI database (Stage 3e, F-1), as the
founder does on docflow-staging in the SQL Editor (RUNBOOK 10.2):

    ALTER ROLE docflow_api WITH LOGIN PASSWORD '...';   -- and the other three

Migration 0036 creates the roles NOLOGIN and gives them every grant, so this
only sets a CI-local password and checks what must hold for each login:
LOGIN, not BYPASSRLS, not SUPERUSER, no CREATE on `public`, and a member of
`docflow_tables` and of no other role -- a policy TO docflow_admin applies to
every member of docflow_admin, so no login may be a member of another.

CI only. Idempotent.

Environment:
  SUPERUSER_DATABASE_URL  the local stack's `postgres` connection string
  APP_ROLE_PASSWORD       the password every login gets (CI-local only)
"""

import os
import sys

import psycopg
from psycopg import sql

LOGINS = ("docflow_api", "docflow_admin", "docflow_worker", "docflow_stripe")


def main() -> int:
    superuser_url = os.environ["SUPERUSER_DATABASE_URL"]
    password = os.environ["APP_ROLE_PASSWORD"]
    problems: list[str] = []

    with psycopg.connect(superuser_url, autocommit=True) as conn:
        for login in LOGINS:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone() is None:
                problems.append(f"{login} does not exist: migration 0036 creates it")
                continue
            conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(login), sql.Literal(password)
                )
            )
            bypass, superuser = conn.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (login,)
            ).fetchone()
            if bypass or superuser:
                problems.append(f"{login} must not be BYPASSRLS or SUPERUSER")
            if conn.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (login,)).scalar():
                problems.append(f"{login} must not have CREATE on schema public")
            member_of = sorted(
                r[0]
                for r in conn.execute(
                    "SELECT g.rolname FROM pg_auth_members m "
                    "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                    "WHERE u.rolname = %s",
                    (login,),
                ).fetchall()
            )
            if member_of != ["docflow_tables"]:
                problems.append(f"{login} must be a member of docflow_tables only, is of {member_of}")

    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"logins ready: {', '.join(LOGINS)} (LOGIN, NOBYPASSRLS, no CREATE, docflow_tables only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
