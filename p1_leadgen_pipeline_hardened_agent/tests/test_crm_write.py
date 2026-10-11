"""Qualified leads reach the CRM in one transaction; conflicts and junk are reported, never lost."""

import logging
import uuid

import psycopg
import pytest

from leadgen.crm_write import (
    QualifiedLead,
    clean_email,
    clean_phone,
    find_contradictions,
    is_junk_email,
    write_qualified_leads,
)
from tests.crm_builders import add_lead, add_market

RUN_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


def qualified(
    market_id: int, name: str = "Cedar Mobile Massage", **columns: object
) -> QualifiedLead:
    values: dict[str, object] = {
        "source": "google_maps",
        "qualification_reason": "Therapists travel to the client's home across the county.",
        "evidence_quote": "We come to you.",
        "source_url": "https://cedar-massage.example/services",
        **columns,
    }
    return QualifiedLead(market_id=market_id, business_name=name, **values)  # type: ignore[arg-type]


def crm_rows(connection: psycopg.Connection) -> list[tuple]:
    return connection.execute(
        """
        SELECT business_name, phone, email, qualification_reason, evidence_quote, evidence_url
        FROM crm_leads ORDER BY lead_id
        """
    ).fetchall()


# --- junk and malformed contact details -------------------------------------------------------


@pytest.mark.parametrize(
    "email",
    [
        "noreply@cedar-massage.com",
        "No-Reply@cedar-massage.com",
        "donotreply@cedar-massage.com",
        "DoNotReply@cedar-massage.com",
        "mailer-daemon@cedar-massage.com",
        "postmaster@cedar-massage.com",
        "hello@example.com",
        "hello@mail.example.org",
        "hello@EXAMPLE.NET",
        "owner@sentry.wixpress.com",
    ],
)
def test_junk_emails_are_recognized(email: str) -> None:
    assert is_junk_email(email)
    assert clean_email(email) is None


@pytest.mark.parametrize(
    "email",
    [
        "hello@cedar-massage.com",
        "hello@cedar.example",  # the reserved .example TLD: the demo seed uses it on purpose
        "replies@cedar-massage.com",  # "replies" is not "reply"-prefixed junk
        "booking@examplemassage.com",  # a real host that merely contains "example"
    ],
)
def test_real_emails_are_kept(email: str) -> None:
    assert not is_junk_email(email)
    assert clean_email(f"  {email} ") == email


@pytest.mark.parametrize("email", ["", "   ", "not-an-email", "two@@at.com", "spaces in@x.com"])
def test_malformed_emails_are_blanked(email: str) -> None:
    assert clean_email(email) is None


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("(310) 555-0142", "(310) 555-0142"),
        ("+1 310 555 0142", "+1 310 555 0142"),
        ("555-0142", None),  # too short to be a 10-digit US key
        ("", None),
        (None, None),
    ],
)
def test_phones_shorter_than_ten_digits_are_blanked(
    phone: str | None, expected: str | None
) -> None:
    assert clean_phone(phone) == expected


# --- the write --------------------------------------------------------------------------------


def test_qualified_leads_are_written_with_their_evidence(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    leads = [
        qualified(market_id, "Cedar Mobile Massage", phone="(310) 555-0142"),
        qualified(market_id, "Harbor Home Massage", email="book@harbor.example"),
    ]

    report = write_qualified_leads(migrated_connection, RUN_ID, leads)

    assert report.inserted == 2
    assert report.conflicts == []
    assert report.skipped_no_contact == []
    assert crm_rows(migrated_connection) == [
        (
            "Cedar Mobile Massage",
            "(310) 555-0142",
            None,
            "Therapists travel to the client's home across the county.",
            "We come to you.",
            "https://cedar-massage.example/services",
        ),
        (
            "Harbor Home Massage",
            None,
            "book@harbor.example",
            "Therapists travel to the client's home across the county.",
            "We come to you.",
            "https://cedar-massage.example/services",
        ),
    ]


def test_junk_email_is_blanked_and_a_lead_with_no_other_contact_is_skipped(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    kept = qualified(market_id, "Cedar", phone="310-555-0142", email="noreply@cedar.com")
    skipped = qualified(market_id, "Harbor", email="noreply@harbor.com")

    report = write_qualified_leads(migrated_connection, RUN_ID, [kept, skipped])

    assert report.inserted == 1
    assert report.skipped_no_contact == [skipped]
    assert [(row[0], row[1], row[2]) for row in crm_rows(migrated_connection)] == [
        ("Cedar", "310-555-0142", None)
    ]


def test_conflicts_with_existing_crm_leads_are_skipped_and_reported(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, business_name="Existing", phone="(310) 555-0142")
    add_lead(migrated_connection, market_id, business_name="Known", email="hi@known.example")
    add_lead(
        migrated_connection,
        market_id,
        business_name="Placed",
        phone="3105550188",
        google_place_id="ChIJplace",
    )

    same_phone_formatted_differently = qualified(market_id, "Dup Phone", phone="+1 310.555.0142")
    same_email_other_case = qualified(market_id, "Dup Email", email="HI@Known.Example")
    same_place = qualified(market_id, "Dup Place", phone="3105550199", google_place_id="ChIJplace")
    new_lead = qualified(market_id, "Brand New", phone="3105550177")

    report = write_qualified_leads(
        migrated_connection,
        RUN_ID,
        [same_phone_formatted_differently, same_email_other_case, same_place, new_lead],
    )

    assert report.inserted == 1
    assert report.conflicts == [same_phone_formatted_differently, same_email_other_case, same_place]
    names = [row[0] for row in crm_rows(migrated_connection)]
    assert names == ["Existing", "Known", "Placed", "Brand New"]


def test_duplicates_inside_one_batch_are_written_once(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    first = qualified(market_id, "First", phone="310-555-0142")
    second = qualified(market_id, "Second", phone="(310) 555 0142")

    report = write_qualified_leads(migrated_connection, RUN_ID, [first, second])

    assert report.inserted_lead_ids != []
    assert report.conflicts == [second]
    assert [row[0] for row in crm_rows(migrated_connection)] == ["First"]


def test_the_batch_is_one_transaction(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection)
    good = qualified(market_id, "Good", phone="310-555-0142")
    # A source the CRM's CHECK constraint rejects: the insert fails mid-batch.
    bad = qualified(market_id, "Bad", phone="310-555-0143", source="carrier_pigeon")

    with pytest.raises(psycopg.errors.CheckViolation):
        write_qualified_leads(migrated_connection, RUN_ID, [good, bad])

    assert crm_rows(migrated_connection) == []


# --- the contradiction scan -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected_phrases"),
    [
        ("Operates a massage school and offers outcall sessions.", ["massage school"]),
        ("Listed on a booking marketplace; therapists travel to clients.", ["booking marketplace"]),
        ("The business is not a massage school; therapists travel to homes.", []),
        ("Not a booking marketplace, a real provider that comes to you.", []),
        ("A mobile provider rather than a massage school.", []),
        ("Therapists travel; there is no mention of a franchise locator.", []),
        ("Operates a massage school, not a directory site.", ["massage school"]),
        ("Serves clients only at its own premises.", ["only at its own premises"]),
        (
            "Isn't a massage school. Does sell equipment: sells equipment online.",
            ["sells equipment"],
        ),
        ("Mobile massage across the city.", []),
    ],
)
def test_contradiction_scan_is_negation_aware(reason: str, expected_phrases: list[str]) -> None:
    assert [phrase for phrase, _clause in find_contradictions(reason)] == expected_phrases


def test_contradictions_are_reported_and_never_block_the_write(
    migrated_connection: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    market_id = add_market(migrated_connection)
    suspicious = qualified(
        market_id,
        "Suspicious",
        phone="310-555-0142",
        qualification_reason="Runs a massage school but also sends therapists to homes.",
    )

    with caplog.at_level(logging.WARNING, logger="leadgen.crm_write"):
        report = write_qualified_leads(migrated_connection, RUN_ID, [suspicious])

    # No eval table exists in this database: the write still lands and the hit is in the report.
    assert report.inserted == 1
    assert [hit.phrase for hit in report.contradictions] == ["massage school"]
    assert report.contradictions[0].lead_id == report.inserted_lead_ids[0]
    assert report.contradictions_recorded is False
    assert "were not recorded in eval_records" in caplog.text
    assert [row[0] for row in crm_rows(migrated_connection)] == ["Suspicious"]


def test_contradictions_are_recorded_in_eval_records_when_the_table_exists(
    migrated_connection: psycopg.Connection,
) -> None:
    # A stand-in with the columns the scan writes; the eval feature owns the real table.
    migrated_connection.execute(
        """
        CREATE TABLE eval_records (
            eval_record_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id         uuid   NOT NULL,
            record_kind    text   NOT NULL,
            lead_id        bigint,
            business_name  text,
            details        text,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    market_id = add_market(migrated_connection)
    suspicious = qualified(
        market_id,
        "Suspicious",
        phone="310-555-0142",
        qualification_reason="Operates a massage school; outcall sessions are listed.",
    )
    clean = qualified(market_id, "Clean", phone="310-555-0143")

    report = write_qualified_leads(migrated_connection, RUN_ID, [suspicious, clean])

    assert report.contradictions_recorded is True
    rows = migrated_connection.execute(
        "SELECT run_id, record_kind, lead_id, business_name, details FROM eval_records"
    ).fetchall()
    assert rows == [
        (
            RUN_ID,
            "contradiction",
            report.inserted_lead_ids[0],
            "Suspicious",
            "reason affirms 'massage school': Operates a massage school",
        )
    ]


def test_report_summary_counts_every_outcome(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, business_name="Existing", phone="3105550142")
    leads = [
        qualified(market_id, "New", phone="3105550177"),
        qualified(market_id, "Dup", phone="3105550142"),
        qualified(market_id, "Unreachable", email="noreply@x.com"),
    ]

    report = write_qualified_leads(migrated_connection, RUN_ID, leads)

    assert report.summary() == (
        f"run {RUN_ID}: 1 inserted, 1 already in the CRM, 1 skipped without a real contact, "
        "0 contradiction hits"
    )
