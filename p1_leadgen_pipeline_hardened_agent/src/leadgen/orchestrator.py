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

from leadgen.audit import (
    BLITZ_ENRICHMENT,
    CONTACT_EXTRACTION,
    CRM_UPLOAD,
    DEDUPE,
    PLANNER,
    SDK_PRE_TOOL_USE_HOOK,
    SDK_RESEARCH_AGENT,
    SDK_TOOL_CALL,
    STATE_MACHINE,
    audit,
)
from leadgen.budget import BudgetBlocked, paid_operation, plan_spend, record_cost, trace
from leadgen.category_config import load_category_config
from leadgen.contacts import BlitzClient, BlitzRequestRejected, enrich_contact
from leadgen.crm_write import QualifiedLead, write_qualified_leads
from leadgen.database import connect
from leadgen.dedupe import IncomingLead, dedupe_against_crm
from leadgen.gaps import read_gaps
from leadgen.job_queue import JobSpec, enqueue_jobs, requeue_failed_jobs, run_worker_pool
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
from leadgen.sourcing_exa import EXA_CEILING_PER_CALL, exa_client, exa_queries, source_exa
from leadgen.sourcing_maps import grade_sample, maps_client, source_maps
from leadgen.web_research import EXA_WEB_CEILING_PER_CALL, PageFetchFailed, build_web_researcher

STAGES = ("maps", "exa", "extract", "dedupe", "enrich", "qualify", "crm")
TOOL_NAME = "mcp__leadgen__run_stage"


def build_plan(connection, category, *, max_cities=1, max_keywords=1, target=50, market=None):
    """Plan the biggest gap, or the named market (e.g. "Phoenix, AZ") if it is below its goal."""
    if not 1 <= max_cities <= 40 or not 1 <= max_keywords <= len(category.keywords) or target < 1:
        raise ValueError("Invalid plan size")
    gaps = read_gaps(connection)
    if market is not None:
        gaps = [
            gap
            for gap in gaps
            if f"{gap.market_name}, {gap.state}".lower() == market.strip().lower()
        ]
        if not gaps:
            raise ValueError(f"{market} is not a market below its lead goal")
    if not gaps:
        raise ValueError("Every market is at its lead goal")
    market = gaps[0]
    places = expand_market(connection, market.market_id, max_places=max_cities)
    if not places:
        raise ValueError("No Census places loaded for the market; run `leadgen db load-places`")
    audit(
        connection,
        PLANNER,
        f"reads demand and picks {market.market_name}, {market.state} "
        f"({market.available_leads}/{market.lead_goal} available, {market.leads_needed} needed)",
        f"{market.market_name}, {market.state}",
        other_markets=[f"{gap.market_name} {gap.leads_available_pct}%" for gap in gaps[1:]],
    )
    audit(
        connection,
        PLANNER,
        f"expands the market into {len(places)} Census cities: "
        + ", ".join(place.label for place in places),
        f"{market.market_name}, {market.state}",
    )
    desired = min(target, math.ceil(market.leads_needed / category.expected_qualification_rate))
    per_cell = math.ceil(desired / (len(places) * max_keywords))
    return create_plan(
        connection,
        [
            PlannedCell(market.market_id, place.label, keyword, per_cell, Decimal("1.00"))
            for place in places
            for keyword in category.keywords[:max_keywords]
        ],
    )


def exa_calls(plan, category):
    """Exa requests the plan will make: one per query phrasing per planned city."""
    cities = list(dict.fromkeys(cell.city for cell in plan.cells))
    return len(exa_queries(category, cities))


def exa_ceiling(plan, category):
    return exa_calls(plan, category) * EXA_CEILING_PER_CALL


def completed_stages(connection, run_id):
    return dict(
        connection.execute(
            "SELECT stage,result FROM pipeline_stages WHERE run_id=%s", (run_id,)
        ).fetchall()
    )


def budget_hook(connection, run_id, plan_id):
    async def hook(input_data, tool_use_id=None, context=None, *, record_allow=True):
        """`record_allow=False` is the handler's second check of a call the SDK's hook already
        allowed and recorded: it records only a denial, so each tool call logs one decision."""
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
        if reason or record_allow:
            trace(connection, run_id, "PreToolUse", decision, {"reason": reason})
            audit(
                connection,
                SDK_PRE_TOOL_USE_HOOK,
                f"{decision}s the tool call ({reason or 'approved stage'})",
                f"run_stage {arguments.get('stage')}",
                run_id=run_id,
                tool=input_data.get("tool_name"),
            )
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
        primary = self.researcher.backend.name
        fallback = self.researcher.fallback.name if self.researcher.fallback else None
        firecrawl_credit = self.settings.firecrawl_usd_per_credit
        if primary == "exa":
            # Exa's own ceiling, plus one Firecrawl scrape if Exa cannot read the page.
            cost = EXA_WEB_CEILING_PER_CALL
            if method == "fetch_page" and fallback == "firecrawl":
                cost += firecrawl_credit
        elif primary == "firecrawl":
            cost = firecrawl_credit * (2 if method == "search" else 1)
        else:
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
                # Only a Firecrawl failure can carry an unknown charge; Exa reports its own.
                if error.charge is None and "firecrawl" in (primary, fallback) and cost:
                    record_cost(
                        self.connection,
                        self.run_id,
                        "firecrawl",
                        firecrawl_credit if primary == "exa" else cost,
                        endpoint="uncertain-fetch",
                        cost_basis="estimated",
                    )
            else:
                if (
                    primary == "firecrawl"
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

    def search(self, query, *, limit=5, site=None):
        return self._call("search", query, limit=limit, site=site)


class Pipeline:
    def __init__(
        self, connection, run, settings, *, maps=None, exa=None, judge=None, research_query=None
    ):
        self.connection, self.run, self.settings = connection, run, settings
        self.category = load_category_config(settings.category_config_path)
        self.maps, self.exa, self.judge = maps, exa, judge
        # The Agent SDK entry point for inline research; tests replace it.
        self.research_query = research_query or query

    async def execute(self, stage):
        connection, run_id = self.connection, self.run.run_id
        done = completed_stages(connection, run_id)
        if stage in done:
            return done[stage]
        expected = next((item for item in STAGES if item not in done), None)
        if stage != expected:
            raise BudgetBlocked("Stage is outside the approved sequence")
        audit(
            connection,
            STATE_MACHINE,
            f"stage {stage} started",
            stage,
            run_id=run_id,
            completed=list(done),
        )
        try:
            result = await getattr(self, f"run_{stage}")()
        except Exception as error:
            audit(
                connection,
                STATE_MACHINE,
                f"stage {stage} failed: {type(error).__name__}",
                stage,
                run_id=run_id,
            )
            raise
        connection.execute(
            "INSERT INTO pipeline_stages(run_id,stage,result) VALUES (%s,%s,%s)",
            (run_id, stage, Jsonb(result)),
        )
        audit(
            connection,
            STATE_MACHINE,
            f"stage {stage} finished: {result}",
            stage,
            run_id=run_id,
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
                verdict = grade_sample(
                    self.connection, self.run.run_id, cell, leads, category=self.category
                )
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
        plan = read_plan(self.connection, self.run.plan_id)
        cells = plan.cells
        for market in sorted({cell.market_id for cell in cells}):
            cities = list(dict.fromkeys(cell.city for cell in cells if cell.market_id == market))
            leads = source_exa(
                self.connection,
                self.run.run_id,
                market,
                self.category,
                self.exa or exa_client(self.settings),
                cities=cities,
            )
            total += len(leads)
        return {
            "sourced": total,
            "queries": exa_calls(plan, self.category),
        }

    async def run_extract(self):
        """Read phones and emails off page text already cached by sourcing; no network calls."""
        from leadgen.contact_extraction import extract_email, extract_phone
        from leadgen.sourcing_exa import homepage_cache_key

        checked = phones = emails = 0
        for lead in read_raw_leads(self.connection, self.run.run_id):
            if not lead["website"] or (lead["phone"] and lead["email"]):
                continue
            row = self.connection.execute(
                "SELECT text FROM page_cache WHERE url=%s", (homepage_cache_key(lead["website"]),)
            ).fetchone()
            if row is None:
                continue
            checked += 1
            phone = None if lead["phone"] else extract_phone(row[0])
            email = None if lead["email"] else extract_email(row[0], lead["website"])
            if phone or email:
                self.connection.execute(
                    "UPDATE raw_leads SET phone=coalesce(phone,%s), email=coalesce(email,%s) "
                    "WHERE raw_lead_id=%s",
                    (phone, email, lead["raw_lead_id"]),
                )
            phones += bool(phone)
            emails += bool(email)
        audit(
            self.connection,
            CONTACT_EXTRACTION,
            f"reads {checked} cached pages: {phones} phones and {emails} emails found",
            "Exa page text",
            run_id=self.run.run_id,
            pages_read=checked,
            phones_found=phones,
            emails_found=emails,
        )
        return {"pages_read": checked, "phones_found": phones, "emails_found": emails}

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
        duplicates = matched_in_crm = 0
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
            matched_in_crm += decision.is_duplicate
        audit(
            self.connection,
            DEDUPE,
            f"keeps {len(leads) - duplicates} new leads and marks {duplicates} as duplicates "
            f"({matched_in_crm} already in the CRM)",
            f"{len(leads)} sourced leads",
            run_id=self.run.run_id,
            new=len(leads) - duplicates,
            duplicates=duplicates,
            matched_in_crm=matched_in_crm,
        )
        return {"duplicates": duplicates, "new": len(leads) - duplicates}

    async def run_enrich(self):
        leads = [
            lead
            for lead in read_raw_leads(self.connection, self.run.run_id, by_consensus=True)
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
                            try:
                                result = enrich_contact(
                                    lead["website"],
                                    connection=connection,
                                    client=client,
                                    run_id=self.run.run_id,
                                    usd_per_record=self.settings.blitz_usd_per_record,
                                )
                            except BlitzRequestRejected as rejection:
                                # BlitzAPI refused this lead's input (e.g. an invalid domain)
                                # before doing any work, so nothing was billed: the lead simply
                                # has no looked-up contact, and the stage carries on.
                                result = None
                                audit(
                                    connection,
                                    BLITZ_ENRICHMENT,
                                    f"rejects {lead['website']}: {rejection}",
                                    lead["business_name"],
                                    run_id=self.run.run_id,
                                    cost_usd=Decimal(0),
                                )
                    if result is not None:
                        connection.execute(
                            "UPDATE raw_leads SET phone=%s,email=%s WHERE raw_lead_id=%s",
                            (result.phone, result.email, lead["raw_lead_id"]),
                        )
                        audit(
                            connection,
                            BLITZ_ENRICHMENT,
                            f"looks up phone and email of {lead['website']}: "
                            f"{'phone' if result.phone else 'no phone'}, "
                            f"{'email' if result.email else 'no email'}",
                            lead["business_name"],
                            run_id=self.run.run_id,
                            found_phone=bool(result.phone),
                            found_email=bool(result.email),
                            from_cache=result.from_cache,
                            cost_usd=(
                                Decimal(0)
                                if result.from_cache
                                else self.settings.blitz_usd_per_record * result.records_used
                            ),
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
            for lead in read_raw_leads(self.connection, self.run.run_id, by_consensus=True)
            if not lead["duplicate"] and not lead["qualification"]
        ]
        # A resumed run retries leads whose earlier attempt failed (its paid calls are reconciled).
        requeue_failed_jobs(self.connection, self.run.run_id, "qualify")
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
                        # Each attempt gets its own reservation keys; the first keeps the old ones.
                        f"tier1:{lead['raw_lead_id']}"
                        + (f":attempt{job.attempts}" if job.attempts > 1 else ""),
                    )
                    evidence = gather_evidence(lead, market, web, config, category=self.category)
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
                verdict = judgment.verdict
                if verdict == "review":
                    # Not confident: escalate this lead to bounded research now, inside the same
                    # job, starting from the evidence tier 1 already gathered.
                    verdict = self.research_inline(
                        connection, lead["raw_lead_id"], market, evidence
                    )
                return {"verdict": verdict, "researched": judgment.verdict == "review"}

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

    def research_inline(self, connection, raw_lead_id, market, evidence):
        """Run the research agent for one review lead and return its final verdict."""
        lead = next(
            row
            for row in read_raw_leads(connection, self.run.run_id)
            if row["raw_lead_id"] == raw_lead_id
        )
        audit(
            connection,
            SDK_RESEARCH_AGENT,
            "starts research (tier 1 said review)",
            lead["business_name"],
            run_id=self.run.run_id,
            reason=lead["qualification"]["reason"],
        )
        with build_web_researcher(connection, self.settings, run_id=self.run.run_id) as researcher:
            web = GuardedWeb(
                connection, self.run.run_id, researcher, self.settings, f"tier2:{raw_lead_id}"
            )
            result = asyncio.run(
                research_lead(
                    connection,
                    self.run.run_id,
                    lead,
                    market,
                    evidence,
                    self.category,
                    self.settings,
                    web,
                    query_fn=self.research_query,
                    judge_client=self.judge,
                )
            )
        return result.verdict if hasattr(result, "verdict") else result["verdict"]

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
        report = write_qualified_leads(
            self.connection,
            self.run.run_id,
            qualified,
            disqualifier_phrases=self.category.disqualifier_phrases,
        )
        audit(
            self.connection,
            CRM_UPLOAD,
            f"inserts {report.inserted}, skips {len(report.skipped_no_contact)} without contact, "
            f"{len(report.conflicts)} conflicts",
            f"{len(qualified)} qualified leads",
            run_id=self.run.run_id,
            leads=[lead.business_name for lead in qualified],
        )
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
    # The orchestrator session's final usage, filed on the run's closing audit row.
    session = {}

    @tool("run_stage", "Run the next approved pipeline stage", {"stage": str})
    async def run_stage(arguments):
        nonlocal stage_failure
        # Enforce the hook's policy again at the handler boundary.
        verdict = await hook({"tool_name": TOOL_NAME, "tool_input": arguments}, record_allow=False)
        if verdict["hookSpecificOutput"]["permissionDecision"] != "allow":
            raise BudgetBlocked(verdict["hookSpecificOutput"]["permissionDecisionReason"])
        try:
            audit(
                connection,
                SDK_TOOL_CALL,
                f"calls run_stage({arguments['stage']!r})",
                arguments["stage"],
                run_id=run.run_id,
                model=settings.leadgen_model,
            )
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
                    session = {
                        "session_subtype": getattr(message, "subtype", None),
                        "session_turns": getattr(message, "num_turns", None),
                        "session_duration_ms": getattr(message, "duration_ms", None),
                        "session_usage": getattr(message, "usage", None),
                        "cost_usd": Decimal(str(message.total_cost_usd or 0)),
                    }
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
        finish_run(connection, run.run_id, "finished", **session)
    except Exception:
        finish_run(connection, run.run_id, "failed", **session)
        raise
    return {
        "run_id": str(run.run_id),
        "stages": completed_stages(connection, run.run_id),
        "cost_usd": str(plan_spend(connection, plan_id)),
    }
