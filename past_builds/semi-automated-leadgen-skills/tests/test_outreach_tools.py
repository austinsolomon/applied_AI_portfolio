"""Business decisions behind the MCP tools; no MCP dependency is needed here."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import socket

import pytest

from pipeline.outreach_tools import OutreachTools


@pytest.fixture
def actions(tmp_path, qualified, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Outreach practice must not open a network connection')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    path = tmp_path / 'qualified.json'
    path.write_text(json.dumps(qualified))
    return OutreachTools(path, tmp_path / 'ledger.sqlite3')


def identifier(actions, name):
    return next(entry['lead']['id'] for entry in json.loads(actions.input_path.read_text())['records']
                if entry['lead']['name'] == name)


def send(actions, name='Sunrise Bakery'):
    lead_id = identifier(actions, name)
    draft = actions.prepare_checked_email(lead_id)
    return actions.send_checked_email(lead_id, draft['draft_id'], draft['recipient'])


def test_prepare_is_not_send_and_send_is_not_delivery(actions):
    lead_id = identifier(actions, 'Sunrise Bakery')
    draft = actions.prepare_checked_email(lead_id)
    assert draft['status'] == 'checked_draft' and draft['counts']['sent'] == 0
    receipt = send(actions)
    assert receipt['sent'] and receipt['delivered'] is None and receipt['replied'] is None
    assert receipt['counts']['delivered'] == 0 and receipt['counts']['meetings'] == 0
    result = actions.read_outreach_results(lead_id)
    assert result['delivered'] and result['replied'] and result['reply_text']
    assert result['counts']['meetings'] == 0


def test_rejected_review_unknown_and_duplicate_ids_are_held(actions, qualified):
    for entry in qualified['rejected_records'] + qualified['review_records']:
        assert not actions.prepare_checked_email(entry['lead']['id'])['ok']
    assert not actions.prepare_checked_email('not-a-lead')['ok']
    data = json.loads(actions.input_path.read_text())
    data['records'].append(deepcopy(data['records'][0]))
    actions.input_path.write_text(json.dumps(data))
    assert not actions.prepare_checked_email(data['records'][0]['lead']['id'])['ok']


def test_four_rules_rechecked_even_if_flag_is_true(actions):
    data = json.loads(actions.input_path.read_text())
    lead = data['records'][0]['lead']
    lead['ordering_platform'] = None
    actions.input_path.write_text(json.dumps(data))
    assert not actions.prepare_checked_email(lead['id'])['ok']


def test_bad_draft_stays_held(actions):
    lead_id = identifier(actions, 'Night Owl Bagels & Bakes')
    draft = actions.prepare_checked_email(lead_id)
    assert not draft['ok'] and draft['reasons'] and draft['counts']['sent'] == 0
    assert not actions.send_checked_email(lead_id, 'invented', 'anyone@example.com')['ok']


def test_address_and_draft_belong_to_the_record(actions):
    lead_id = identifier(actions, 'Sunrise Bakery')
    draft = actions.prepare_checked_email(lead_id)
    for address in ['', 'somebody-else@example.com']:
        assert not actions.send_checked_email(lead_id, draft['draft_id'], address)['ok']
    assert not actions.send_checked_email(lead_id, 'invented', draft['recipient'])['ok']
    assert not actions.read_outreach_results(lead_id)['ok']


def test_changed_record_requires_new_check(actions):
    lead_id = identifier(actions, 'Sunrise Bakery')
    draft = actions.prepare_checked_email(lead_id)
    data = json.loads(actions.input_path.read_text())
    entry = next(e for e in data['records'] if e['lead']['id'] == lead_id)
    entry['lead']['review_count'] += 1
    actions.input_path.write_text(json.dumps(data))
    assert not actions.send_checked_email(lead_id, draft['draft_id'], draft['recipient'])['ok']


def test_receipt_describes_the_record_when_sent(actions):
    receipt = send(actions)
    data = json.loads(actions.input_path.read_text())
    data['records'] = [entry for entry in data['records'] if entry['lead']['id'] != receipt['lead_id']]
    actions.input_path.write_text(json.dumps(data))
    result = actions.read_outreach_results(receipt['lead_id'])
    assert result['ok'] and result['replied']
    assert not actions.prepare_checked_email(receipt['lead_id'])['ok']


def test_only_separate_matching_confirmation_records_a_meeting(actions):
    lead_id = identifier(actions, 'Sunrise Bakery')
    assert not actions.record_confirmed_meeting(lead_id, 'invented', 'yes')['ok']
    send(actions)
    result = actions.read_outreach_results(lead_id)
    event = result['confirmation_evidence']
    assert not actions.record_confirmed_meeting(lead_id, 'wrong time', event['confirmation'])['ok']
    assert not actions.record_confirmed_meeting(lead_id, event['starts_at'], 'yes')['ok']
    first = actions.record_confirmed_meeting(lead_id, event['starts_at'], event['confirmation'])
    again = actions.record_confirmed_meeting(lead_id, event['starts_at'], event['confirmation'])
    assert first['ok'] and again['already_recorded']
    assert again['counts']['meetings'] == 1 and again['counts']['customers'] == 0


@pytest.mark.parametrize('bakery', ['Honeycomb Oven', 'Bluebird Bakery', 'Riverbend Bread'])
def test_interest_delivery_and_bounce_cannot_invent_a_booking(actions, bakery):
    receipt = send(actions, bakery)
    lead_id = receipt['lead_id']
    result = actions.read_outreach_results(lead_id)
    assert result['confirmation_evidence'] is None
    assert not actions.record_confirmed_meeting(lead_id, '2026-09-24T10:00:00-05:00', 'yes')['ok']


def test_repeated_concurrent_and_restarted_requests_send_once(actions):
    lead_id = identifier(actions, 'Sunrise Bakery')
    draft = actions.prepare_checked_email(lead_id)
    def submit(_):
        return OutreachTools(actions.input_path, actions.state_path).send_checked_email(
            lead_id, draft['draft_id'], draft['recipient'])
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(submit, range(8)))
    assert all(receipt['ok'] for receipt in receipts)
    assert len({receipt['message_id'] for receipt in receipts}) == 1
    assert sum(not receipt['already_recorded'] for receipt in receipts) == 1
    assert submit(None)['counts']['sent'] == 1


def test_same_file_is_not_used_for_input_and_state(tmp_path):
    with pytest.raises(ValueError):
        OutreachTools(tmp_path / 'same.json', tmp_path / 'same.json')
