"""Create or rotate the production database logins and store their URLs in Secret Manager.

Run after `leadgen db migrate` on a new production database, and after any migration that adds a
dashboard view. A re-run only reapplies grants (and creates a login that is missing); `--rotate`
also replaces every password (then update the Streamlit Cloud secret):
    DATABASE_URL="$(gcloud secrets versions access latest --secret=DATABASE_URL)" \\
        uv run python scripts/provision_database_roles.py --project leadgen-prod-austin

DATABASE_URL must be the database owner's connection (it creates roles and grants). The logins
and what each may do are defined in leadgen.database_roles. Passwords are generated here and
written straight to Secret Manager; nothing is printed.
"""

import argparse
import os
import secrets
import subprocess
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from leadgen.database_roles import GRANTS, ROLES


def role_url(owner_url, role, password):
    parts = urlsplit(owner_url)
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{role}:{quote(password, safe='')}@{host}"))


def store_secret(project, name, value):
    exists = (
        subprocess.run(
            ["gcloud", "secrets", "describe", name, f"--project={project}"], capture_output=True
        ).returncode
        == 0
    )
    command = (
        ["gcloud", "secrets", "versions", "add", name]
        if exists
        else ["gcloud", "secrets", "create", name, "--replication-policy=automatic"]
    )
    subprocess.run(
        [*command, "--data-file=-", f"--project={project}"],
        input=value.encode(),
        capture_output=True,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="GCP project holding the secrets")
    parser.add_argument("--rotate", action="store_true", help="replace every password")
    arguments = parser.parse_args()
    owner_url = os.environ["DATABASE_URL"]
    new_passwords = {}
    with psycopg.connect(owner_url, autocommit=True) as connection:
        database = sql.Identifier(connection.info.dbname)
        for role, grant in GRANTS.items():
            missing = not connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            if missing or arguments.rotate:
                new_passwords[role] = secrets.token_urlsafe(32)
                statement = (
                    "CREATE ROLE {} WITH LOGIN PASSWORD {}"
                    if missing
                    else "ALTER ROLE {} PASSWORD {}"
                )
                connection.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(role), sql.Literal(new_passwords[role])
                    )
                )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, sql.Identifier(role))
            )
            grant(connection)
    for role, password in new_passwords.items():
        store_secret(arguments.project, ROLES[role], role_url(owner_url, role, password))
    print(f"grants applied; new passwords stored for: {', '.join(new_passwords) or 'none'}")


if __name__ == "__main__":
    main()
