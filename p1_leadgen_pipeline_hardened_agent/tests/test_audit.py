"""The audit trail names exactly 19 actors, and no call site can write a row for another."""

import ast
from pathlib import Path

import psycopg
import pytest

from leadgen import audit as audit_module
from leadgen import web_research
from leadgen.audit import ACTORS, audit, component_for_operation

SOURCE = Path(audit_module.__file__).parent
# Wrappers that pass their own `actor` parameter straight to audit(); their callers are checked.
PASSTHROUGH_WRAPPERS = {"_audit"}
# Lookups a wrapper's caller may use for the actor; every value is checked below.
ACTOR_TABLES = {
    "SEARCH_AUDIT_ACTORS": web_research.SEARCH_AUDIT_ACTORS,
    "FETCH_AUDIT_ACTORS": web_research.FETCH_AUDIT_ACTORS,
}


def actor_arguments():
    """(location, actor expression) for every audit() and wrapper call in the package."""
    for path in sorted(SOURCE.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "audit" and len(node.args) >= 2:
                yield f"{path.name}:{node.lineno}", node.args[1]
            elif name in PASSTHROUGH_WRAPPERS and node.args:
                yield f"{path.name}:{node.lineno}", node.args[0]


def actor_value(expression):
    """The actor string(s) an expression can produce, or None when it cannot be checked."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return [expression.value]
    if isinstance(expression, ast.Name) and hasattr(audit_module, expression.id):
        return [getattr(audit_module, expression.id)]
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "get"
        and getattr(expression.func.value, "id", None) in ACTOR_TABLES
    ):
        return list(ACTOR_TABLES[expression.func.value.id].values())
    return None


def test_actors_are_19_tool_role_names():
    assert len(ACTORS) == len(set(ACTORS)) == 19
    for actor in ACTORS:
        tool, _, role = actor.partition(" (")
        assert tool and role.endswith(")"), actor


def test_every_call_site_uses_one_of_the_19_actors():
    sites = list(actor_arguments())
    assert len(sites) > 30, "the scan found the audit call sites"
    for location, expression in sites:
        if location.startswith("web_research.py") and getattr(expression, "id", None) == "actor":
            continue  # WebResearcher._audit passing its parameter on; its callers are checked.
        values = actor_value(expression)
        assert values is not None, f"{location}: actor {ast.unparse(expression)} is not checkable"
        for value in values:
            assert value in ACTORS, f"{location}: {value!r} is not one of the 19 audit actors"


def test_an_unknown_actor_is_skipped_without_raising(migrated_connection, monkeypatch):
    monkeypatch.setenv("LEADGEN_AUDIT", "1")
    audit(migrated_connection, "Exa stage", "sends company search", "Exa API")
    audit(
        migrated_connection,
        "Exa API (Search)",
        "company search returns 3 results",
        "financial planners",
    )
    rows = migrated_connection.execute("SELECT actor FROM audit_events").fetchall()
    assert rows == [("Exa API (Search)",)]


def test_audit_never_raises_on_a_broken_connection(migrated_connection, monkeypatch):
    monkeypatch.setenv("LEADGEN_AUDIT", "1")
    migrated_connection.close()
    with pytest.raises(psycopg.OperationalError):
        migrated_connection.execute("SELECT 1")
    audit(migrated_connection, "Postgres (State machine)", "run finished", "plan 1")


@pytest.mark.parametrize(
    ("operation", "actor"),
    [
        ("maps:1:sample", "Apify API (Google Maps)"),
        ("exa:1:financial planners in Phoenix", "Exa API (Search)"),
        ("web:tier1:7:2:search", "Exa API (Search)"),
        ("web:tier1:7:1:fetch_page", "Exa API (Contents)"),
        ("judge:1:7", "Claude API (LLM call)"),
        ("sdk:orchestrator:1", "Claude Agent SDK (Tool call)"),
        ("sdk:research:7", "Claude Agent SDK (Research agent)"),
        ("enrich:7", "BlitzAPI (Enrichment)"),
    ],
)
def test_a_reservation_names_the_actor_whose_call_it_covers(operation, actor):
    assert component_for_operation(operation) == actor
