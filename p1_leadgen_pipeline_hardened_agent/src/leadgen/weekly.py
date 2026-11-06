"""`leadgen weekly`: one unattended run approved by a standing policy instead of a person.

The steps, each of which can stop the run with a plain reason:

1. Ready check: migrations current, no run in progress, no paid call left uncertain.
2. Duplicate guard: a policy-approved plan in the last `min_hours_between_runs` hours means
   nothing to do, so a repeated trigger never pays twice. The schedule sets the cadence.
3. Gaps: no market below its goal means nothing to do.
4. Plan within policy: the planner's Maps and Exa ceilings must leave room under the budget.
5. Approval recorded as `policy:weekly@<policy file sha>`, through the same `approve_plan` a
   person uses, so the stage gate and reservations enforce it exactly as they would a human's.
6. The approved plan runs through the same stage loop (`run_plan`) as `leadgen run`.
7. A summary goes to stdout, to traces and, if SLACK_WEBHOOK_URL is set, to Slack.
"""

import hashlib
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field

from leadgen.audit import POLICY_APPROVAL, audit
from leadgen.budget import plan_spend, trace
from leadgen.category_config import load_category_config
from leadgen.gaps import read_gaps
from leadgen.migrate import MigrationError, _check_history, discover_migrations
from leadgen.orchestrator import Pipeline, build_plan, exa_ceiling, run_plan
from leadgen.plans import approve_plan
from leadgen.run_closeout import settle_ended_runs, settles_by_estimate, web_service_for
from leadgen.run_status import _market_label

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_DO = 2
EXIT_NEEDS_HUMAN = 3

DEFAULT_POLICY_PATH = Path("config/weekly_policy.yaml")
APPROVER_PREFIX = "policy:weekly@"


class WeeklyPolicy(BaseModel):
    """`config/weekly_policy.yaml`, validated like the category file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    budget_usd: Decimal = Field(gt=0, le=25, allow_inf_nan=False)
    max_cities: int = Field(default=1, ge=1, le=40)
    max_keywords: int = Field(default=1, ge=1)
    target_leads: int = Field(default=30, ge=1, le=500)
    min_hours_between_runs: int = Field(default=12, ge=0, le=744)
    market: str | None = None


class PolicyError(Exception):
    """The policy file is missing or invalid."""


class NeedsHuman(Exception):
    """The run must not start until an operator acts; the message says what to do."""


def policy_version(raw: bytes) -> str:
    """The file's git blob id (what `git hash-object` prints), shortened. Computed without git so
    it works inside the container; it matches the committed file's id in the repository."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()[:12]


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> tuple[WeeklyPolicy, str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PolicyError(f"cannot read {path}: {error.strerror}") from error
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise PolicyError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise PolicyError(f"{path} must hold a mapping of policy fields")
    try:
        return WeeklyPolicy.model_validate(document), policy_version(raw)
    except ValueError as error:
        raise PolicyError(f"{path} is not a valid policy: {error}") from error


def check_ready(
    connection: psycopg.Connection,
    *,
    web_service: str = "firecrawl",
    claude_model: str = "unknown",
    settle: bool = True,
) -> None:
    """Refuse to start anything a person has to look at first. Never migrates by itself.

    Paid calls an ended run left unpriced are booked at their ceilings first (`settle=False`, the
    dry run, only previews that), so a finished run never blocks the next one. An Apify actor
    that may still be running does block: only an operator can confirm it stopped."""
    migrations = discover_migrations()
    try:
        applied = dict(
            connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        )
        _check_history(migrations, applied)
    except (psycopg.errors.UndefinedTable, MigrationError) as error:
        raise NeedsHuman(f"Database schema problem: {error}. Run `leadgen db migrate`.") from error
    pending = [migration.name for migration in migrations if migration.version not in applied]
    if pending:
        raise NeedsHuman(f"{len(pending)} migrations are not applied. Run `leadgen db migrate`.")
    running = connection.execute("SELECT run_id FROM runs WHERE status='running'").fetchall()
    if running:
        raise NeedsHuman(
            f"Run {running[0][0]} is still marked running. If no process is executing it, "
            "reconcile its paid calls (docs/operations.md) before starting another."
        )
    if settle:
        settle_ended_runs(connection, web_service=web_service, claude_model=claude_model)
    open_calls = connection.execute(
        "SELECT plan_id, operation, status FROM budget_reservations "
        "WHERE status IN ('started','uncertain') ORDER BY plan_id, reservation_id"
    ).fetchall()
    blocking = [
        (plan_id, operation)
        for plan_id, operation, status in open_calls
        if status == "started" or not settles_by_estimate(operation)
    ]
    if blocking:
        listed = ", ".join(f"plan {plan_id}: {operation}" for plan_id, operation in blocking)
        raise NeedsHuman(
            f"An Apify actor may still be running ({listed}). Confirm in Apify that it stopped, "
            "then reconcile it (docs/operations.md)."
        )


def recent_policy_approval(connection, hours: int):
    if hours == 0:
        return None
    return connection.execute(
        "SELECT plan_id, approved_at FROM approvals WHERE approved_by LIKE %s "
        "AND approved_at > now() - make_interval(hours => %s) ORDER BY approved_at DESC LIMIT 1",
        (APPROVER_PREFIX + "%", hours),
    ).fetchone()


def summarize(connection, plan_id: int, budget_usd: Decimal) -> dict:
    run = connection.execute(
        "SELECT run_id, status FROM runs WHERE plan_id=%s ORDER BY started_at DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    summary = {
        "plan_id": plan_id,
        "market": _market_label(connection, plan_id),
        "budget_usd": str(budget_usd),
        "spend_usd": str(plan_spend(connection, plan_id)),
    }
    if run is None:
        return {**summary, "run_id": None, "status": "not started"}
    run_id, status = run
    sourced = dict(
        connection.execute(
            "SELECT source, count(*) FROM raw_leads WHERE run_id=%s GROUP BY 1", (run_id,)
        ).fetchall()
    )
    verdicts = dict(
        connection.execute(
            "SELECT coalesce(qualification->>'verdict','not judged'), count(*) FROM raw_leads "
            "WHERE run_id=%s AND NOT duplicate GROUP BY 1",
            (run_id,),
        ).fetchall()
    )
    crm = connection.execute(
        "SELECT result FROM pipeline_stages WHERE run_id=%s AND stage='crm'", (run_id,)
    ).fetchone()
    spend = connection.execute(
        "SELECT service, sum(cost_usd) FROM cost_ledger WHERE run_id=%s GROUP BY 1 ORDER BY 1",
        (run_id,),
    ).fetchall()
    return {
        **summary,
        "run_id": str(run_id),
        "status": status,
        "sourced": {"google_maps": sourced.get("google_maps", 0), "exa": sourced.get("exa", 0)},
        "duplicates": connection.execute(
            "SELECT count(*) FROM raw_leads WHERE run_id=%s AND duplicate", (run_id,)
        ).fetchone()[0],
        "qualified": verdicts.get("qualified", 0),
        "review": verdicts.get("review", 0),
        "rejected": verdicts.get("rejected", 0),
        "not_judged": verdicts.get("not judged", 0),
        "crm_inserted": (crm[0] or {}).get("inserted", 0) if crm else 0,
        "spend_by_service": {service: str(cost) for service, cost in spend},
    }


def post_to_slack(webhook_url: str, summary: dict, *, http_client=None) -> bool:
    """Best effort: a Slack failure never fails the run."""
    text = (
        f"leadgen weekly: {summary.get('status')} · {summary.get('market')} · "
        f"qualified {summary.get('qualified', 0)}, CRM +{summary.get('crm_inserted', 0)} · "
        f"${summary.get('spend_usd')} of ${summary.get('budget_usd')}"
    )
    try:
        client = http_client or httpx.Client(timeout=10)
        try:
            response = client.post(webhook_url, json={"text": text})
        finally:
            if http_client is None:
                client.close()
        return response.status_code < 400
    except Exception:
        return False


async def run_weekly(
    connection,
    settings,
    policy: WeeklyPolicy,
    version: str,
    *,
    dry_run: bool = False,
    query_fn=None,
    pipeline_factory=Pipeline,
) -> tuple[int, dict]:
    try:
        check_ready(
            connection,
            web_service=web_service_for(settings),
            claude_model=settings.leadgen_model,
            settle=not dry_run,
        )
    except NeedsHuman as reason:
        return EXIT_NEEDS_HUMAN, {"status": "needs a human", "reason": str(reason)}
    recent = recent_policy_approval(connection, policy.min_hours_between_runs)
    if recent and not dry_run:
        return EXIT_NOTHING_TO_DO, {
            "status": "nothing to do",
            "reason": f"plan {recent[0]} was policy-approved at {recent[1].isoformat()}, "
            f"within {policy.min_hours_between_runs} hours",
        }
    if not read_gaps(connection):
        return EXIT_NOTHING_TO_DO, {"status": "nothing to do", "reason": "every market is at goal"}
    category = load_category_config(settings.category_config_path)
    approver = APPROVER_PREFIX + version

    def plan_within_policy():
        plan = build_plan(
            connection,
            category,
            max_cities=policy.max_cities,
            max_keywords=min(policy.max_keywords, len(category.keywords)),
            target=policy.target_leads,
            market=policy.market,
        )
        estimate = plan.estimated_cost_usd + exa_ceiling(plan, category)
        return (
            plan,
            estimate,
            {
                "plan_id": plan.plan_id,
                "market": _market_label(connection, plan.plan_id),
                "cities": [cell.city for cell in plan.cells],
                "keywords": sorted({cell.keyword for cell in plan.cells}),
                "estimated_sourcing_ceiling_usd": str(estimate),
                "budget_usd": str(policy.budget_usd),
                "approver": approver,
            },
        )

    if dry_run:
        # Plan inside a transaction that is rolled back: nothing is stored, nothing is spent.
        with connection.transaction(force_rollback=True):
            _plan, estimate, preview = plan_within_policy()
        within = estimate < policy.budget_usd
        status = "dry run: would run" if within else "dry run: plan exceeds policy"
        if recent:
            status = (
                f"dry run: nothing to do (plan {recent[0]} was approved within the duplicate guard)"
            )
        return (EXIT_OK if within else EXIT_NEEDS_HUMAN), {
            **preview,
            "plan_id": None,
            "status": status,
        }

    try:
        plan, estimate, preview = plan_within_policy()
    except ValueError as error:
        # A pinned market that already met its goal is nothing to do; anything else (no Census
        # places, an invalid plan size) is configuration a person has to fix.
        nothing = "lead goal" in str(error)
        return (EXIT_NOTHING_TO_DO if nothing else EXIT_NEEDS_HUMAN), {
            "status": "nothing to do" if nothing else "needs a human",
            "reason": str(error),
        }
    if estimate >= policy.budget_usd:
        return EXIT_NEEDS_HUMAN, {
            **preview,
            "status": "needs a human",
            "reason": f"plan ceilings ${estimate} leave no room under the ${policy.budget_usd} "
            "policy budget; raise budget_usd or shrink the plan",
        }
    approve_plan(connection, plan.plan_id, policy.budget_usd, approver)
    audit(
        connection,
        POLICY_APPROVAL,
        f"approves plan {plan.plan_id} with a ${policy.budget_usd} budget under the "
        "weekly policy (no person in the loop)",
        f"plan {plan.plan_id}",
        plan_id=plan.plan_id,
        approved_by=approver,
    )
    options = {"pipeline_factory": pipeline_factory}
    if query_fn is not None:
        options["query_fn"] = query_fn
    try:
        await run_plan(connection, plan.plan_id, settings, **options)
        exit_code = EXIT_OK
    except Exception as error:
        exit_code = EXIT_ERROR
        failure = f"{type(error).__name__}: {error}"
    summary = summarize(connection, plan.plan_id, policy.budget_usd)
    summary["approver"] = approver
    if exit_code != EXIT_OK:
        summary["error"] = failure
    if summary.get("run_id"):
        trace(connection, summary["run_id"], "weekly", "summary", summary)
    return exit_code, summary
