"""Least-privilege database logins for production, shared by the provisioning script and tests.

- app: the weekly job. Reads and writes rows in every table; cannot change the schema.
- dashboard: a public or shared dashboard. Reads only the aggregate views in DASHBOARD_VIEWS.
- operator: the private operator dashboard (every tab). Reads every table; writes only eval
  labels.

Grants for tables added later by the owner's migrations follow through default privileges, except
the dashboard's views, which are granted one by one: rerun the provisioning script after a
migration that adds one.
"""

from psycopg import sql

from leadgen.run_status import DASHBOARD_VIEWS, OPERATOR_WRITABLE_TABLES

ROLES = {
    "leadgen_app": "APP_DATABASE_URL",
    "leadgen_dashboard": "DASHBOARD_DATABASE_URL",
    "leadgen_operator": "OPERATOR_DATABASE_URL",
}

_APP = [
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
    "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {role}",
]

_OPERATOR = [
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}",
]


def _run(connection, statements, role):
    for statement in statements:
        connection.execute(sql.SQL(statement).format(role=sql.Identifier(role)))


def grant_app(connection, role="leadgen_app"):
    _run(connection, _APP, role)


def grant_dashboard(connection, role="leadgen_dashboard"):
    _run(connection, ["GRANT USAGE ON SCHEMA public TO {role}"], role)
    for view in DASHBOARD_VIEWS:
        connection.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(view), sql.Identifier(role))
        )


def grant_operator(connection, role="leadgen_operator"):
    _run(connection, _OPERATOR, role)
    for table in OPERATOR_WRITABLE_TABLES:
        connection.execute(
            sql.SQL("GRANT INSERT, UPDATE ON {} TO {}").format(
                sql.Identifier(table), sql.Identifier(role)
            )
        )


GRANTS = {
    "leadgen_app": grant_app,
    "leadgen_dashboard": grant_dashboard,
    "leadgen_operator": grant_operator,
}
