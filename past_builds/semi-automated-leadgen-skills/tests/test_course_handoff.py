import json
from pathlib import Path
import pytest
from pipeline.import_research import import_answers
from pipeline.run_pipeline import main
from pipeline.providers.base import env_key


def test_explicit_offline_ignores_saved_keys(monkeypatch, tmp_path):
    monkeypatch.setenv('PIPELINE_OFFLINE', '0')
    for key in ('APIFY_TOKEN', 'APOLLO_API_KEY', 'HUNTER_API_KEY', 'ANTHROPIC_API_KEY', 'EMAIL_SENDER_API_KEY'):
        monkeypatch.setenv(key, 'not-a-real-key')
    main(['--offline', '--outdir', str(tmp_path)])
    assert env_key('APIFY_TOKEN') == (None, None)
    funnel = json.loads((tmp_path / 'funnel.json').read_text())
    assert not funnel['live']
    assert [funnel['stages'][k] for k in ('leads', 'qualified', 'sent', 'inboxed')] == [23, 9, 8, 5]
    assert (funnel['replies'], funnel['meetings'], funnel['customers']) == (2, 1, 0)


def test_import_keeps_human_review_and_known_values(monkeypatch, tmp_path):
    monkeypatch.setenv('PIPELINE_OFFLINE', '1')
    main(['--offline', '--outdir', str(tmp_path)])
    before = json.loads((tmp_path / 'enriched.json').read_text())
    repo = Path(__file__).resolve().parents[1]
    answers = json.loads((repo / 'fixtures/research_agent_answers.json').read_text())
    after, count = import_answers(before, answers)
    assert count == 2
    rye = next(r for r in after['records'] if r['name'] == 'Rye & Rosemary Bakery')
    assert rye['ordering_platform'] is None
    assert rye['enrichment']['research']['status'] == 'awaiting_check'
    dos = next(r for r in after['records'] if r['name'] == 'Dos Rios Panaderia')
    assert dos['enrichment']['research']['status'] == 'unclear'
    assert [r['ordering_platform'] for r in before['records']] == [r['ordering_platform'] for r in after['records']]
    bad = json.loads(json.dumps(answers))
    bad['answers'][0]['evidence']['source_url'] = 'https://uncited.example'
    with pytest.raises(ValueError, match='cited source'):
        import_answers(before, bad)


def test_skills_are_discoverable_and_match_game_copies():
    repo = Path(__file__).resolve().parents[1]
    for path in (repo / 'skills').glob('*/SKILL.md'):
        installed = repo / '.claude/skills' / path.parent.name.replace('_', '-') / 'SKILL.md'
        assert installed.read_text() == path.read_text()
        assert '--offline' in installed.read_text()


def test_draft_export_calls_no_sender_even_when_a_sender_key_exists(monkeypatch, qualified):
    from pipeline.outreach import run_outreach
    from pipeline.providers.bakery_outreach_fixture import BakeryTemplateCopyProvider
    monkeypatch.setenv('EMAIL_SENDER_API_KEY', 'not-a-real-key')
    result = run_outreach(qualified['records'], copy_provider=BakeryTemplateCopyProvider(), drafts_only=True)
    assert result['mode'] == 'drafts_only'
    assert len(result['drafts']) == 8
    assert result['counts']['held'] == 1
    assert result['counts']['sent'] == result['counts']['meetings'] == 0
    assert result['records'] == result['meetings'] == []


def test_mixed_provider_run_cannot_claim_live_delivery_or_fixture_meeting(qualified):
    from pipeline.outreach import run_outreach
    from pipeline.providers.bakery_outreach_fixture import BakeryTemplateCopyProvider, BakeryFixtureSendProvider
    class PretendLiveCopy(BakeryTemplateCopyProvider):
        live = True
    result = run_outreach(qualified['records'], copy_provider=PretendLiveCopy(), send_provider=BakeryFixtureSendProvider())
    assert result['live'] is False
    assert result['uses_live_provider'] is True
    assert result['counts']['meetings'] == 0
