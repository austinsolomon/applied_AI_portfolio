"""Approved-plan runner: a deterministic stage loop with durable checkpoints.

The pipeline's order never changes, so plain code runs it. The Claude Agent SDK is used only where
the next step is not known in advance: adapting sourcing to what the first searches returned, and
researching a lead the judge could not decide."""

import asyncio
import math
from decimal import Decimal

from claude_agent_sdk import query
from psycopg.types.json import Jsonb

from leadgen.audit import (
    BLITZ_ENRICHMENT,
    CONTACT_EXTRACTION,
    CRM_UPLOAD,
    DEDUPE,
    PLANNER,
    SDK_RESEARCH_AGENT,
    STAGE_GATE,
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
from leadgen.research_agent import research_lead
from leadgen.run_closeout import close_out_run, settle_unknown_outcomes, web_service_for
from leadgen.sourcing_exa import EXA_CEILING_PER_CALL, exa_client, exa_queries, source_exa
from leadgen.sourcing_maps import grade_sample, maps_client, source_maps
from leadgen.web_research import (
    EXA_WEB_CEILING_PER_CALL,
    PageFetchFailed,
    WebBackendUnavailable,
    build_web_researcher,
)

STAGES = ("maps", "exa", "extract", "dedupe", "enrich", "qualify", "crm")


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


def stage_gate(connection, run_id, plan_id, stage):
    """The check before every stage: it is the next one in order, and budget remains.

    Returns the reason a stage may not run, or None when it may."""
    done = completed_stages(connection, run_id)
    expected = next((item for item in STAGES if item not in done), None)
    approval = read_approval(connection, plan_id)
    if stage != expected:
        reason = "Stage is outside the approved sequence"
    elif not approval or plan_spend(connection, plan_id) >= approval.budget_usd:
        reason = "Approved budget is exhausted or missing"
    else:
        return None
    trace(connection, run_id, "stage_gate", "deny", {"stage": stage, "reason": reason})
    audit(connection, STAGE_GATE, f"blocks the stage ({reason})", stage, run_id=run_id)
    return reason


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
            except WebBackendUnavailable as error:
                # A timeout or 5xx: the request is over, only its charge is unknown. Book the
                # whole reserved ceiling so the reservation settles instead of staying uncertain
                # (callers such as evidence gathering skip the page and carry on).
                failure = error
                shares = (
                    {"exa": EXA_WEB_CEILING_PER_CALL, "firecrawl": cost - EXA_WEB_CEILING_PER_CALL}
                    if primary == "exa"
                    else {"firecrawl": cost}
                )
                for service, share in shares.items():
                    if share > 0:
                        record_cost(
                            self.connection,
                            self.run_id,
                            service,
                            share,
                            endpoint="unavailable-call",
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
        self, connection, run, settings, *, maps=None, exa=None, judge=None, agent_query=None
    ):
        self.connection, self.run, self.settings = connection, run, settings
        self.category = load_category_config(settings.category_config_path)
        self.maps, self.exa, self.judge = maps, exa, judge
        # The Agent SDK entry point for both agents (sourcing, research); tests replace it.
        self.agent_query = agent_query or query

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
            f"stage {stage} finished: "
            + ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in result.items()),
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
        judged_before = self.keys_judged_in_earlier_runs({lead["market_id"] for lead in leads})
        seen = set()
        duplicates = matched_in_crm = already_judged = 0
        for lead, decision in zip(leads, decisions, strict=True):
            keys = self.identity_keys(decision)
            # A business an earlier run already judged for this market is not judged (or paid
            # for) again; a reviewer can still revisit its earlier decision.
            judged = not decision.is_duplicate and any(key in judged_before for key in keys)
            duplicate = decision.is_duplicate or judged or any(key in seen for key in keys)
            already_judged += judged
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
            f"({matched_in_crm} already in the CRM, {already_judged} judged in an earlier run)",
            f"{len(leads)} sourced leads",
            run_id=self.run.run_id,
            new=len(leads) - duplicates,
            duplicates=duplicates,
            matched_in_crm=matched_in_crm,
            already_judged=already_judged,
        )
        return {
            "duplicates": duplicates,
            "new": len(leads) - duplicates,
            "already_judged": already_judged,
        }

    def identity_keys(self, decision):
        """The keys that identify one business: place ID, phone, email, and its own domain."""
        from leadgen.category_config import is_on_host

        keys = [
            (key, getattr(decision.keys, key))
            for key in ("google_place_id", "phone", "email")
            if getattr(decision.keys, key)
        ]
        domain = decision.keys.website_domain
        if domain and not is_on_host(domain, self.category.hosts_never_identifying_a_business):
            keys.append(("domain", domain))
        return keys

    def keys_judged_in_earlier_runs(self, market_ids):
        """Identity keys of every business an earlier run judged in these markets, normalized by
        the same CRM expressions dedupe uses."""
        if not market_ids:
            return set()
        rows = self.connection.execute(
            "SELECT business_name,google_place_id,phone,email,website FROM raw_leads "
            "WHERE market_id = ANY(%s) AND run_id <> %s AND qualification IS NOT NULL",
            (list(market_ids), self.run.run_id),
        ).fetchall()
        earlier = [
            IncomingLead(
                business_name=name, google_place_id=place, phone=phone, email=email, website=site
            )
            for name, place, phone, email, site in rows
        ]
        return {
            key
            for decision in dedupe_against_crm(self.connection, earlier, self.category)
            for key in self.identity_keys(decision)
        }

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
                    query_fn=self.agent_query,
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
    connection, plan_id, settings, *, query_fn=None, pipeline_factory=Pipeline, resume=False
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
    if query_fn is not None:
        # The agents' Agent SDK entry point (tests pass a replay).
        pipeline.agent_query = query_fn
    try:
        for stage in STAGES:
            if stage in completed_stages(connection, run.run_id):
                continue
            reason = stage_gate(connection, run.run_id, plan_id, stage)
            if reason:
                raise BudgetBlocked(reason)
            try:
                await pipeline.execute(stage)
            except Exception as error:
                trace(connection, run.run_id, stage, "failed", {"error_type": type(error).__name__})
                raise
        # The completion check: a run is finished only when it leaves nothing open behind it.
        close_out_run(
            connection,
            run.run_id,
            STAGES,
            web_service=web_service_for(settings),
            claude_model=settings.leadgen_model,
        )
        finish_run(connection, run.run_id, "finished")
    except Exception:
        try:
            # Calls that are over but unpriced settle even when the run fails; only an Apify
            # actor that may still be running is left for the operator.
            settle_unknown_outcomes(
                connection,
                run.run_id,
                web_service=web_service_for(settings),
                claude_model=settings.leadgen_model,
            )
        finally:
            finish_run(connection, run.run_id, "failed")
        raise
    return {
        "run_id": str(run.run_id),
        "stages": completed_stages(connection, run.run_id),
        "cost_usd": str(plan_spend(connection, plan_id)),
    }
