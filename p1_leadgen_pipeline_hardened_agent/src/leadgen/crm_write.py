"""Write judge-qualified leads into the CRM, in one transaction, without ever losing a conflict.

Only leads the qualification judge marked `qualified` reach this module. Each one must still be
contactable: at least one real phone or email. Junk emails (`noreply@…`, `@example.com`) are
blanked before the check, so a lead whose only contact is a mailbox nobody reads is skipped and
reported rather than written.

Every lead is inserted with `INSERT … ON CONFLICT DO NOTHING`, so a business the CRM already holds
under any unique key (Google place ID, phone, email) is skipped by the database itself and counted
as a conflict. The whole batch is one transaction: a run either writes all of its new leads or
none of them.

After the write, a contradiction scan reads each written lead's `qualification_reason`. A reason
that affirms a disqualifier ("operates a massage school") while the verdict is `qualified` is a
judge inconsistency worth a human look. The scan is negation-aware: "is not a massage school" is
no hit. Hits go to `eval_records` and never block the write.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

import psycopg

logger = logging.getLogger(__name__)

# Email local parts (before the "@") that nobody reads. Matched as a prefix, case-insensitively.
JUNK_EMAIL_LOCAL_PART_PREFIXES: tuple[str, ...] = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "mailer-daemon",
    "postmaster",
)

# Email hosts that never belong to a real business. The reserved `example.com` family is junk; the
# reserved `.example` top-level domain is NOT listed because the demo seed uses it on purpose
# (`hello@cedar.example`), and a host is matched exactly or as a subdomain of an entry.
JUNK_EMAIL_HOSTS: tuple[str, ...] = (
    "example.com",
    "example.net",
    "example.org",
    "email.com",
    "domain.com",
    "test.com",
    "sentry.io",
    "sentry.wixpress.com",
    "wixpress.com",
)

# Same shape the CRM's CHECK constraint enforces on `email_normalized`.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Phrases that, affirmed in a qualification reason, describe a business the rubric rejects.
# They are kept here rather than parsed from the rubric prose so the scan is explicit and testable.
DEFAULT_DISQUALIFIER_PHRASES: tuple[str, ...] = (
    "massage school",
    "franchise locator",
    "equipment seller",
    "supplies seller",
    "sells equipment",
    "sells supplies",
    "booking marketplace",
    "business directory",
    "online directory",
    "directory site",
    "adult service",
    "adult services",
    "only at its own premises",
    "only serves clients at",
    "studio only",
    "in-studio only",
    "no mobile service",
    "does not travel",
)

# A disqualifier phrase preceded by one of these within the same clause is negated, not affirmed.
NEGATION_MARKERS: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "neither",
        "nor",
        "without",
        "isn't",
        "aren't",
        "wasn't",
        "doesn't",
        "don't",
        "didn't",
        "cannot",
        "can't",
        "won't",
        "rather",
        "instead",
        "unlike",
        "non",
    }
)
NEGATION_WINDOW_WORDS = 5
CLAUSE_SPLIT = re.compile(r"[.;:!?,()]|\bbut\b|\bhowever\b|\balthough\b|\bwhereas\b", re.IGNORECASE)


@dataclass(frozen=True)
class QualifiedLead:
    """A lead the judge qualified, with the contact columns as sourcing delivered them."""

    market_id: int
    business_name: str
    source: str  # 'google_maps' or 'exa', as the CRM's CHECK constraint allows
    qualification_reason: str
    google_place_id: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    evidence_quote: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ContradictionHit:
    """A written lead whose qualification reason affirms a disqualifier."""

    lead_id: int
    business_name: str
    phrase: str
    clause: str


@dataclass
class CrmWriteReport:
    """What one `write_qualified_leads` call did, lead by lead. Nothing is silently dropped."""

    run_id: UUID
    inserted_lead_ids: list[int] = field(default_factory=list)
    conflicts: list[QualifiedLead] = field(default_factory=list)
    skipped_no_contact: list[QualifiedLead] = field(default_factory=list)
    contradictions: list[ContradictionHit] = field(default_factory=list)
    contradictions_recorded: bool = False

    @property
    def inserted(self) -> int:
        return len(self.inserted_lead_ids)

    def summary(self) -> str:
        return (
            f"run {self.run_id}: {self.inserted} inserted, {len(self.conflicts)} already in the "
            f"CRM, {len(self.skipped_no_contact)} skipped without a real contact, "
            f"{len(self.contradictions)} contradiction hits"
        )


def is_junk_email(email: str) -> bool:
    """True for mailboxes nobody reads and reserved hosts; see the lists above."""
    normalized = email.strip().lower()
    local_part, separator, host = normalized.rpartition("@")
    if not separator:
        return True
    if local_part.startswith(JUNK_EMAIL_LOCAL_PART_PREFIXES):
        return True
    return any(
        host == junk_host or host.endswith(f".{junk_host}") for junk_host in JUNK_EMAIL_HOSTS
    )


def clean_email(email: str | None) -> str | None:
    """The email trimmed, or None when it is blank, malformed or junk."""
    if email is None:
        return None
    trimmed = email.strip()
    if not trimmed or not EMAIL_PATTERN.match(trimmed) or is_junk_email(trimmed):
        return None
    return trimmed


def clean_phone(phone: str | None) -> str | None:
    """The phone as given, or None when it cannot become a 10-digit US key in the CRM.

    The CRM keeps the last 10 digits of a phone and rejects anything shorter; blanking such
    phones here keeps one bad number from failing the whole batch.
    """
    if phone is None:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    return phone.strip()


def clean_contact(lead: QualifiedLead) -> QualifiedLead:
    """The lead with junk and malformed contact details blanked."""
    return replace(lead, phone=clean_phone(lead.phone), email=clean_email(lead.email))


def has_real_contact(lead: QualifiedLead) -> bool:
    return lead.phone is not None or lead.email is not None


def find_contradictions(
    reason: str, disqualifier_phrases: Sequence[str] = DEFAULT_DISQUALIFIER_PHRASES
) -> list[tuple[str, str]]:
    """Disqualifier phrases the reason affirms, as (phrase, clause) pairs.

    The reason is split into clauses; a phrase counts only when no negation marker appears in the
    few words before it inside its clause. "is a massage school" hits; "is not a massage school"
    and "a studio, not a mobile service" do not.
    """
    hits: list[tuple[str, str]] = []
    for clause in CLAUSE_SPLIT.split(reason):
        clause_text = clause.strip()
        if not clause_text:
            continue
        lowered = clause_text.lower()
        for phrase in disqualifier_phrases:
            for match in re.finditer(rf"\b{re.escape(phrase.lower())}\b", lowered):
                preceding_words = re.findall(r"[a-z']+", lowered[: match.start()])
                window = preceding_words[-NEGATION_WINDOW_WORDS:]
                if not any(word in NEGATION_MARKERS for word in window):
                    hits.append((phrase, clause_text))
                    break
    return hits


INSERT_LEAD = """
INSERT INTO crm_leads (
    market_id, business_name, source, phone, email, website, google_place_id,
    qualification_reason, evidence_quote, evidence_url
)
VALUES (
    %(market_id)s, %(business_name)s, %(source)s, %(phone)s, %(email)s, %(website)s,
    %(google_place_id)s, %(qualification_reason)s, %(evidence_quote)s, %(source_url)s
)
ON CONFLICT DO NOTHING
RETURNING lead_id
"""

# Written after the batch commits, so a missing or differently shaped eval table can never undo a
# CRM write. The eval tables belong to the qualification and eval-review features; this insert
# names the columns the contradiction scan needs and is the one place to reconcile them.
INSERT_CONTRADICTION = """
INSERT INTO eval_records (run_id, record_kind, lead_id, business_name, details)
VALUES (%(run_id)s, 'contradiction', %(lead_id)s, %(business_name)s, %(details)s)
"""


def write_qualified_leads(
    connection: psycopg.Connection,
    run_id: UUID,
    leads: Sequence[QualifiedLead],
    disqualifier_phrases: Sequence[str] = DEFAULT_DISQUALIFIER_PHRASES,
) -> CrmWriteReport:
    """Insert `leads` into `crm_leads` in one transaction and report every outcome.

    Leads without a real phone or email after cleaning are skipped. Leads the CRM already holds
    under a unique key are left untouched by `ON CONFLICT DO NOTHING` and reported as conflicts.
    The contradiction scan runs on what was written and is recorded best-effort afterwards.
    """
    report = CrmWriteReport(run_id=run_id)
    written: list[tuple[int, QualifiedLead]] = []

    with connection.transaction():
        for raw_lead in leads:
            lead = clean_contact(raw_lead)
            if not has_real_contact(lead):
                report.skipped_no_contact.append(raw_lead)
                continue
            row = connection.execute(
                INSERT_LEAD,
                {
                    "market_id": lead.market_id,
                    "business_name": lead.business_name,
                    "source": lead.source,
                    "phone": lead.phone,
                    "email": lead.email,
                    "website": lead.website,
                    "google_place_id": lead.google_place_id,
                    "qualification_reason": lead.qualification_reason,
                    "evidence_quote": lead.evidence_quote,
                    "source_url": lead.source_url,
                },
            ).fetchone()
            if row is None:
                report.conflicts.append(raw_lead)
                continue
            report.inserted_lead_ids.append(row[0])
            written.append((row[0], lead))

    for lead_id, lead in written:
        for phrase, clause in find_contradictions(lead.qualification_reason, disqualifier_phrases):
            report.contradictions.append(
                ContradictionHit(lead_id, lead.business_name, phrase, clause)
            )

    report.contradictions_recorded = record_contradictions(
        connection, run_id, report.contradictions
    )
    logger.info(report.summary())
    return report


def record_contradictions(
    connection: psycopg.Connection, run_id: UUID, hits: Sequence[ContradictionHit]
) -> bool:
    """Append the hits to `eval_records`. Returns False, and only logs, when that fails.

    The CRM write has already committed by the time this runs; an eval table that is missing or
    shaped differently must not turn a successful write into an error.
    """
    if not hits:
        return True
    try:
        with connection.transaction():
            for hit in hits:
                connection.execute(
                    INSERT_CONTRADICTION,
                    {
                        "run_id": run_id,
                        "lead_id": hit.lead_id,
                        "business_name": hit.business_name,
                        "details": f"reason affirms '{hit.phrase}': {hit.clause}",
                    },
                )
    except psycopg.Error as error:
        logger.warning(
            "contradiction hits for run %s were not recorded in eval_records (%s): %s",
            run_id,
            type(error).__name__,
            "; ".join(f"{hit.business_name}: {hit.phrase}" for hit in hits),
        )
        return False
    return True
