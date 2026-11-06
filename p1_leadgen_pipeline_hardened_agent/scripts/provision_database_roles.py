"""Create or rotate the production database logins and store their URLs in Secret Manager.

Run after `leadgen db migrate` on a new production database, and after any migration that adds a
dashboard view. A re-run only reapplies grants; `--rotate` also replaces both passwords (then
update the Streamlit Cloud secret):
    DATABASE_URL="$(gcloud secrets versions access latest --secret=DATABASE_URL)" \
        uv run python scripts/provision_database_roles.py --project leadgen-prod-austin

DATABASE_URL must be the database owner's connection (it creates roles and grants). Two logins:
- leadgen_app: the weekly job. Reads and writes rows in every table; cannot change the schema.
  Its URL is stored as the APP_DATABASE_URL secret.
- leadgen_dashboard: the hosted dashboard. Reads only the aggregate views in DASHBOARD_VIEWS,
  which hold no phone numbers, emails or page text. Its URL is stored as DASHBOARD_DATABASE_URL.

Passwords are generated here and written straight to Secret Manager; nothing is printed.
"""

import argparse
import os
import secrets
import subprocess
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from leadgen.run_status import DASHBOARD_VIEWS  # market and run aggregates only, no contacts

APP_GRANTS = [
    "GRANT USAGE ON SCHEMA public TO leadgen_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO leadgen_app",
    "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO leadgen_app",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO leadgen_app",
    # Tables and sequences added by later migrations (run as the owner) get the same rights.
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO leadgen_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO leadgen_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO leadgen_app",
]


def role_exists(connection, role):
    return connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()


def set_password(connection, role, password, *, create):
    statement = "CREATE ROLE {} WITH LOGIN PASSWORD {}" if create else "ALTER ROLE {} PASSWORD {}"
    connection.execute(sql.SQL(statement).format(sql.Identifier(role), sql.Literal(password)))


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
    parser.add_argument("--rotate", action="store_true", help="replace both passwords")
    arguments = parser.parse_args()
    owner_url = os.environ["DATABASE_URL"]
    new_passwords = {}
    with psycopg.connect(owner_url, autocommit=True) as connection:
        database = connection.info.dbname
        for role in ("leadgen_app", "leadgen_dashboard"):
            missing = not role_exists(connection, role)
            if missing or arguments.rotate:
                new_passwords[role] = secrets.token_urlsafe(32)
                set_password(connection, role, new_passwords[role], create=missing)
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
        for statement in APP_GRANTS:
            connection.execute(statement)
        connection.execute("GRANT USAGE ON SCHEMA public TO leadgen_dashboard")
        for view in DASHBOARD_VIEWS:
            connection.execute(
                sql.SQL("GRANT SELECT ON {} TO leadgen_dashboard").format(sql.Identifier(view))
            )
    secret_names = {
        "leadgen_app": "APP_DATABASE_URL",
        "leadgen_dashboard": "DASHBOARD_DATABASE_URL",
    }
    for role, password in new_passwords.items():
        store_secret(arguments.project, secret_names[role], role_url(owner_url, role, password))
    print(f"grants applied; new passwords stored for: {', '.join(new_passwords) or 'none'}")


if __name__ == "__main__":
    main()
