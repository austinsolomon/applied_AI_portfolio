"""Approved-plan SDK runner with a deterministic stage order and durable checkpoints."""

import asyncio
import json
import math
from decimal import Decimal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)
from psycopg.types.json import Jsonb

from leadgen.budget import BudgetBlocked, paid_operation, plan_spend, record_cost, trace
from leadgen.category_config import load_category_config
from leadgen.contacts import BlitzClient, enrich_contact
from leadgen.crm_write import QualifiedLead, write_qualified_leads
from leadgen.database import connect
from leadgen.dedupe import IncomingLead, dedupe_against_crm
from leadgen.gaps import read_gaps
from leadgen.job_queue import JobSpec, enqueue_jobs, run_worker_pool
from leadgen.places import expand_market
from leadgen.plans import (
    PlannedCell,
    create_plan,
    finish_cell_attempt,
    finish_run,
    read_approval,
    read_plan,
    read_run,
    start_cell_attempt,
    start_run,
)
from leadgen.qualification import gather_evidence, judge_lead
from leadgen.raw_leads import read_raw_leads
from leadgen.research_agent import research_lead, sdk_environment
from leadgen.sourcing_exa import exa_client, source_exa
from leadgen.sourcing_maps import grade_sample, maps_client, source_maps
from leadgen.web_research import PageFetchFailed, build_web_researcher

STAGES = ("maps", "exa", "dedupe", "enrich", "qualify", "research", "crm")
TOOL_NAME = "mcp__leadgen__run_stage"


def build_plan(connection, category, *, max_cities=1, max_keywords=1, target=50):
    if not 1 <= max_cities <= 40 or not 1 <= max_keywords <= len(category.keywords) or target < 1:
        raise ValueError("Invalid plan size")
    gaps = read_gaps(connection)
    if not gaps:
        raise ValueError("Every market is at its lead goal")
    market = gaps[0]
    places = expand_market(connection, market.market_id, max_places=max_cities)
    if not places:
        raise ValueError("No Census places loaded for the market; run `leadgen db load-places`")
    desired = min(target, math.ceil(market.short_by_leads / category.expected_qualification_rate))
    per_cell = math.ceil(desired / (len(places) * max_keywords))
    return create_plan(
        connection,
        [
            PlannedCell(market.market_id, place.label, keyword, per_cell, Decimal("1.00"))
            for place in places
            for keyword in category.keywords[:max_keywords]
        ],
    )


def completed_stages(connection, run_id):
    return dict(
        connection.execute(
            "SELECT stage,result FROM pipeline_stages WHERE run_id=%s", (run_id,)
        ).fetchall()
    )


def budget_hook(connection, run_id, plan_id):
    async def hook(input_data, tool_use_id=None, context=None):
        reason = None
        arguments = input_data.get("tool_input", {})
        done = completed_stages(connection, run_id)
        expected = next((stage for stage in STAGES if stage not in done), None)
        approval = read_approval(connection, plan_id)
        if input_data.get("tool_name") != TOOL_NAME:
            reason = "Tool is outside the approved plan"
        elif arguments.get("stage") != expected or set(arguments) != {"stage"}:
            reason = "Stage is outside the approved sequence"
        elif not approval or plan_spend(connection, plan_id) >= approval.budget_usd:
            reason = "Approved budget is exhausted or missing"
        decision = "deny" if reason else "allow"
        trace(connection, run_id, "PreToolUse", decision, {"reason": reason})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason or "Approved stage",
            }
        }

    return hook


class GuardedWeb:
    """Each paid web request reserves its own upper bound; cached requests still cost nothing."""

    def __init__(self, connection, run_id, researcher, settings, prefix):
        self.connection, self.run_id, self.researcher = connection, run_id, researcher
        self.settings, self.prefix, self.count = settings, prefix, 0

    def _call(self, method, argument, **kwargs):
        self.count += 1
        cost = self.settings.firecrawl_usd_per_credit * (2 if method == "search" else 1)
        if self.researcher.backend.name != "firecrawl":
            cost = Decimal(0)
        failure = None
        with paid_operation(
            self.connection, self.run_id, f"web:{self.prefix}:{self.count}:{method}", cost
        ):
            try:
                result = getattr(self.researcher, method)(argument, **kwargs)
            except PageFetchFailed as error:
                # A failed page is a known result. If billing is unknown, account for its ceiling.
                failure = error
                if cost and error.charge is None:
                    record_cost(
                        self.connection,
                        self.run_id,
                        "firecrawl",
                        cost,
                        endpoint="uncertain-fetch",
                        cost_basis="estimated",
                    )
            else:
                if (
                    cost
                    and method == "fetch_page"
                    and result.backend == "free"
                    and not result.from_cache
                ):
                    record_cost(
                        self.connection,
                        self.run_id,
                        "firecrawl",
                        cost,
                        endpoint="fallback-fetch",
                        cost_basis="estimated",
                    )
        if failure:
            raise failure
        return result

    def fetch_page(self, url):
        return self._call("fetch_page", url)

    def search(self, query, *, limit=5):
        return self._call("search", query, limit=limit)


class Pipeline:
    def __init__(self, connection, run, settings, *, maps=None, exa=None, judge=None):
        self.connection, self.run, self.settings = connection, run, settings
        self.category = load_category_config(settings.category_config_path)
        self.maps, self.exa, self.judge = maps, exa, judge

    async def execute(self, stage):
        connection, run_id = self.connection, self.run.run_id
        done = completed_stages(connection, run_id)
        if stage in done:
            return done[stage]
        expected = next((item for item in STAGES if item not in done), None)
        if stage != expected:
            raise BudgetBlocked("Stage is outside the approved sequence")
        result = await getattr(self, f"run_{stage}")()
        connection.execute(
            "INSERT INTO pipeline_stages(run_id,stage,result) VALUES (%s,%s,%s)",
            (run_id, stage, Jsonb(result)),
        )
        trace(connection, run_id, stage, "succeeded", result)
        return result

    async def run_maps(self):
        total = 0
        for cell in read_plan(self.connection, self.run.plan_id).cells:
            if cell.status != "planned":
                continue
            attempt = start_cell_attempt(self.connection, self.run.run_id, cell.cell_id, "sample")
            try:
                client = self.maps or maps_client(self.settings)
                sample_limit = min(10, cell.expected_raw_leads)
                sample_budget = (
                    cell.estimated_cost_usd
                    if sample_limit == cell.expected_raw_leads
                    else cell.estimated_cost_usd / 2
                )
                leads = source_maps(
                    self.connection,
                    self.run.run_id,
                    cell,
                    client,
                    limit=sample_limit,
                    ceiling=sample_budget,
                )
                verdict = grade_sample(self.connection, self.run.run_id, cell, leads)
                if verdict == "PROCEED" and len(leads) < cell.expected_raw_leads:
                    refreshed = next(
                        item
                        for item in read_plan(self.connection, self.run.plan_id).cells
                        if item.cell_id == cell.cell_id
                    )
                    full_attempt = start_cell_attempt(
                        self.connection, self.run.run_id, cell.cell_id, "full"
                    )
                    leads = source_maps(
                        self.connection,
                        self.run.run_id,
                        refreshed,
                        client,
                        limit=cell.expected_raw_leads,
                        ceiling=cell.estimated_cost_usd - sample_budget,
                        stage="full",
                    )
                    finish_cell_attempt(
                        self.connection,
                        full_attempt.attempt_id,
                        succeeded=True,
                        leads_found=len(leads),
                    )
                    self.connection.execute(
                        "UPDATE scrape_plan_cells SET status='scraped' WHERE cell_id=%s",
                        (cell.cell_id,),
                    )
                finish_cell_attempt(
                    self.connection, attempt.attempt_id, succeeded=True, leads_found=len(leads)
                )
                total += len(leads)
            except Exception:
                finish_cell_attempt(
                    self.connection,
                    attempt.attempt_id,
                    succeeded=False,
                    error="Sourcing failed; inspect provider run before resuming",
                )
                raise
        return {"sourced": total}

    async def run_exa(self):
        total = 0
        markets = {cell.market_id for cell in read_plan(self.connection, self.run.plan_id).cells}
        for market in markets:
            total += len(
                source_exa(
                    self.connection,
                    self.run.run_id,
                    market,
                    self.category,
                    self.exa or exa_client(self.settings),
                )
            )
        return {"sourced": total}

    async def run_dedupe(self):
        leads = read_raw_leads(self.connection, self.run.run_id)
        incoming = [
            IncomingLead(
                **{
                    key: lead[key]
                    for key in ("business_name", "google_place_id", "phone", "email", "website")
                }
            )
            for lead in leads
        ]
        decisions = dedupe_against_crm(self.connection, incoming, self.category)
        seen = set()
        duplicates = 0
        for lead, decision in zip(leads, decisions, strict=True):
            from leadgen.category_config import is_on_host

            keys = [
                (key, getattr(decision.keys, key))
                for key in ("google_place_id", "phone", "email")
                if getattr(decision.keys, key)
            ]
            domain = decision.keys.website_domain
            if domain and not is_on_host(domain, self.category.hosts_never_identifying_a_business):
                keys.append(("domain", domain))
            duplicate = decision.is_duplicate or any(key in seen for key in keys)
            seen.update(keys)
            self.connection.execute(
                "UPDATE raw_leads SET duplicate=%s WHERE raw_lead_id=%s",
                (duplicate, lead["raw_lead_id"]),
            )
            duplicates += duplicate
        return {"duplicates": duplicates, "new": len(leads) - duplicates}

    async def run_enrich(self):
        leads = [
            lead
            for lead in read_raw_leads(self.connection, self.run.run_id)
            if not lead["duplicate"] and not lead["enriched"]
        ]
        enqueue_jobs(
            self.connection,
            self.run.run_id,
            "enrich",
            [
                JobSpec(str(lead["raw_lead_id"]), {"lead": lead["raw_lead_id"]}, max_attempts=1)
                for lead in leads
            ],
        )

        def enrich_one(job):
            with connect(str(self.settings.database_url)) as connection:
                lead = next(
                    row
                    for row in read_raw_leads(connection, self.run.run_id)
                    if row["raw_lead_id"] == job.payload["lead"]
                )
                # Existing contact details do not need paid enrichment.
                if lead["website"] and not (lead["phone"] or lead["email"]):
                    if not self.settings.blitz_api_key:
                        raise ValueError("BLITZ_API_KEY is not set")
                    with paid_operation(
                        connection,
                        self.run.run_id,
                        f"enrich:{lead['raw_lead_id']}",
                        4 * self.settings.blitz_usd_per_record,
                    ):
                        with BlitzClient(
                            self.settings.blitz_api_key.get_secret_value(), max_retries=0
                        ) as client:
                            result = enrich_contact(
                                lead["website"],
                                connection=connection,
                                client=client,
                                run_id=self.run.run_id,
                                usd_per_record=self.settings.blitz_usd_per_record,
                            )
                        connection.execute(
                            "UPDATE raw_leads SET phone=%s,email=%s WHERE raw_lead_id=%s",
                            (result.phone, result.email, lead["raw_lead_id"]),
                        )
                connection.execute(
                    "UPDATE raw_leads SET enriched=true WHERE raw_lead_id=%s",
                    (lead["raw_lead_id"],),
                )
            return {"enriched": True}

        async def handler(job):
            return await asyncio.to_thread(enrich_one, job)

        report = await run_worker_pool(
            str(self.settings.database_url),
            handler,
            run_id=self.run.run_id,
            kinds=["enrich"],
            concurrency=2,
        )
        if report.failed:
            raise ValueError("Enrichment failed; inspect cell_jobs before resuming")
        return {"processed": report.succeeded}

    async def run_qualify(self):
        leads = [
            lead
            for lead in read_raw_leads(self.connection, self.run.run_id)
            if not lead["duplicate"] and not lead["qualification"]
        ]
        enqueue_jobs(
            self.connection,
            self.run.run_id,
            "qualify",
            [
                JobSpec(str(lead["raw_lead_id"]), {"lead": lead["raw_lead_id"]}, max_attempts=1)
                for lead in leads
            ],
        )

        def qualify_one(job):
            with connect(str(self.settings.database_url)) as connection:
                lead = next(
                    row
                    for row in read_raw_leads(connection, self.run.run_id)
                    if row["raw_lead_id"] == job.payload["lead"]
                )
                if lead["qualification"]:
                    return {"already_judged": True}
                row = connection.execute(
                    "SELECT name,state FROM markets WHERE market_id=%s", (lead["market_id"],)
                ).fetchone()
                market = f"{row[0]}, {row[1]}"
                config = connection.execute(
                    "SELECT config FROM agent_columns WHERE column_name='qualification'"
                ).fetchone()[0]
                with build_web_researcher(
                    connection, self.settings, run_id=self.run.run_id
                ) as researcher:
                    web = GuardedWeb(
                        connection,
                        self.run.run_id,
                        researcher,
                        self.settings,
                        f"tier1:{lead['raw_lead_id']}",
                    )
                    evidence = gather_evidence(lead, market, web, config)
                judgment = judge_lead(
                    connection,
                    self.run.run_id,
                    lead,
                    market,
                    evidence,
                    self.category,
                    self.settings,
                    client=self.judge,
                )
                return {"verdict": judgment.verdict}

        async def handler(job):
            return await asyncio.to_thread(qualify_one, job)

        report = await run_worker_pool(
            str(self.settings.database_url),
            handler,
            run_id=self.run.run_id,
            kinds=["qualify"],
            concurrency=4,
        )
        if report.failed:
            raise ValueError("Qualification jobs failed; inspect cell_jobs and provider usage")
        return {"judged": report.succeeded}

    async def run_research(self):
        count = 0
        for lead in read_raw_leads(self.connection, self.run.run_id):
            if (
                lead["duplicate"]
                or lead["researched"]
                or not lead["qualification"]
                or lead["qualification"]["verdict"] != "review"
            ):
                continue
            with build_web_researcher(
                self.connection, self.settings, run_id=self.run.run_id
            ) as researcher:
                web = GuardedWeb(
                    self.connection,
                    self.run.run_id,
                    researcher,
                    self.settings,
                    f"tier2:{lead['raw_lead_id']}",
                )
                await research_lead(
                    self.connection,
                    self.run.run_id,
                    lead,
                    self.market_label(lead["market_id"]),
                    {},
                    self.category,
                    self.settings,
                    web,
                )
            count += 1
        return {"researched": count}

    async def run_crm(self):
        qualified = []
        for lead in read_raw_leads(self.connection, self.run.run_id):
            judgment = lead["qualification"]
            if lead["duplicate"] or not judgment or judgment["verdict"] != "qualified":
                continue
            qualified.append(
                QualifiedLead(
                    **{
                        key: lead[key]
                        for key in (
                            "market_id",
                            "business_name",
                            "source",
                            "google_place_id",
                            "phone",
                            "email",
                            "website",
                        )
                    },
                    qualification_reason=judgment["reason"],
                    evidence_quote=judgment["evidence_quote"],
                    source_url=judgment["source_url"],
                )
            )
        report = write_qualified_leads(self.connection, self.run.run_id, qualified)
        return {
            "inserted": report.inserted,
            "conflicts": len(report.conflicts),
            "without_contact": len(report.skipped_no_contact),
        }

    def market_label(self, market_id):
        row = self.connection.execute(
            "SELECT name,state FROM markets WHERE market_id=%s", (market_id,)
        ).fetchone()
        return f"{row[0]}, {row[1]}"


async def run_plan(
    connection, plan_id, settings, *, query_fn=query, pipeline_factory=Pipeline, resume=False
):
    previous = connection.execute(
        "SELECT run_id,status FROM runs WHERE plan_id=%s ORDER BY started_at DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    if previous:
        if previous[1] == "finished":
            return {
                "run_id": str(previous[0]),
                "already_finished": True,
                "stages": completed_stages(connection, previous[0]),
            }
        if not resume or previous[1] != "failed":
            raise BudgetBlocked("This plan has an unfinished run. Reconcile it, then use --resume.")
        if connection.execute(
            "SELECT 1 FROM budget_reservations WHERE plan_id=%s "
            "AND status IN ('started','uncertain')",
            (plan_id,),
        ).fetchone():
            raise BudgetBlocked("Unresolved paid operations must be reconciled before resuming")
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    if previous:
        updated = connection.execute(
            "UPDATE runs SET status='running',finished_at=NULL WHERE run_id=%s "
            "AND status='failed' RETURNING run_id",
            (previous[0],),
        ).fetchone()
        if updated is None:
            raise BudgetBlocked("Another worker already resumed this run")
        run = read_run(connection, previous[0])
    else:
        run = start_run(connection, plan_id)
    pipeline = pipeline_factory(connection, run, settings)
    hook = budget_hook(connection, run.run_id, plan_id)

    stage_failure = None

    @tool("run_stage", "Run the next approved pipeline stage", {"stage": str})
    async def run_stage(arguments):
        nonlocal stage_failure
        # Enforce the hook's policy again at the handler boundary.
        verdict = await hook({"tool_name": TOOL_NAME, "tool_input": arguments})
        if verdict["hookSpecificOutput"]["permissionDecision"] != "allow":
            raise BudgetBlocked(verdict["hookSpecificOutput"]["permissionDecisionReason"])
        try:
            result = await pipeline.execute(arguments["stage"])
        except Exception as error:
            stage_failure = str(error)
            trace(
                connection,
                run.run_id,
                arguments["stage"],
                "failed",
                {"error_type": type(error).__name__},
            )
            raise
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    server = create_sdk_mcp_server(name="leadgen", tools=[run_stage])
    ceiling = Decimal("0.08")
    try:
        session_number = (
            connection.execute(
                "SELECT count(*) FROM budget_reservations WHERE run_id=%s "
                "AND operation LIKE 'sdk:orchestrator%%'",
                (run.run_id,),
            ).fetchone()[0]
            + 1
        )
        with paid_operation(connection, run.run_id, f"sdk:orchestrator:{session_number}", ceiling):
            options = ClaudeAgentOptions(
                model=settings.leadgen_model,
                tools=[],
                allowed_tools=[TOOL_NAME],
                mcp_servers={"leadgen": server},
                strict_mcp_config=True,
                setting_sources=[],
                hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
                max_turns=settings.leadgen_max_turns,
                max_budget_usd=float(ceiling),
                env=sdk_environment(settings),
                permission_mode="dontAsk",
                system_prompt="Execute approved stages exactly once in order. Never invent stages.",
            )
            result_seen = False
            remaining_stages = [
                stage for stage in STAGES if stage not in completed_stages(connection, run.run_id)
            ]
            async for message in query_fn(
                prompt=f"Plan {plan_id}: run remaining stages: {', '.join(remaining_stages)}.",
                options=options,
            ):
                if isinstance(message, ResultMessage):
                    result_seen = True
                    record_cost(
                        connection,
                        run.run_id,
                        "claude",
                        Decimal(str(message.total_cost_usd or 0)),
                        model=settings.leadgen_model,
                        endpoint="orchestrator",
                    )
                    if message.is_error:
                        raise ValueError(
                            "Agent stopped before completion; inspect traces and credits"
                        )
            if not result_seen:
                raise ValueError("Agent did not report its final usage; reconciliation required")
        if len(completed_stages(connection, run.run_id)) != len(STAGES):
            raise ValueError(stage_failure or "Agent ended before all pipeline stages completed")
        finish_run(connection, run.run_id, "finished")
    except Exception:
        finish_run(connection, run.run_id, "failed")
        raise
    return {
        "run_id": str(run.run_id),
        "stages": completed_stages(connection, run.run_id),
        "cost_usd": str(plan_spend(connection, plan_id)),
    }
