"""Stage states for the dashboard's Current run tab."""

from leadgen.run_status import stage_states


def statuses(run_status, completed, unresolved=False):
    return [state.status for state in stage_states(run_status, completed, unresolved)]


def test_a_running_run_marks_the_next_stage_running_and_the_rest_waiting():
    completed = {"maps": ({"sourced": 18}, None), "exa": ({"sourced": 37}, None)}
    assert statuses("running", completed) == [
        "done",
        "done",
        "running",
        "waiting",
        "waiting",
        "waiting",
        "waiting",
        "waiting",
    ]


def test_a_failed_run_is_blocked_only_while_a_paid_call_is_unresolved():
    completed = {"maps": ({}, None)}
    assert statuses("failed", completed, unresolved=True)[1] == "blocked"
    assert statuses("failed", completed, unresolved=False)[1] == "failed"


def test_a_finished_run_shows_every_stage_done():
    stages = ("maps", "exa", "adapt", "extract", "dedupe", "enrich", "qualify", "crm")
    completed = {stage: ({}, None) for stage in stages}
    assert set(statuses("finished", completed)) == {"done"}


def test_a_run_finished_before_the_sourcing_agent_shows_that_stage_skipped():
    stages = ("maps", "exa", "extract", "dedupe", "enrich", "qualify", "crm")
    completed = {stage: ({}, None) for stage in stages}
    assert statuses("finished", completed)[2] == "skipped"
