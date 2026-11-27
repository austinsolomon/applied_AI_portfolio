"""Four local Outreach actions, shared by the MCP server and its offline tests.

Only saved course receipts are used. No provider is selected from environment
keys. SQLite keeps repeated requests from creating duplicate sends or meetings.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .meetings import confirmed_meetings, FIXTURE as MEETING_FIXTURE
from .outreach import lint
from .providers.bakery_outreach_fixture import (
    BakeryFixtureSendProvider, BakeryTemplateCopyProvider, OFFER,
    outreach_row_for, DESIGNED_SEND_ONCE_A_REVIEW_IS_CLEARED,
)
from .qualify import ICP

REPLY_FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/outreach_replies.json'
EMAIL = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class OutreachTools:
    """A fixed local input file, four actions, and a separate practice ledger."""

    def __init__(self, input_path='out/qualified.json', state_path='out/mcp_outreach.sqlite3'):
        self.input_path = Path(input_path)
        self.state_path = Path(state_path)
        if self.input_path.resolve() == self.state_path.resolve():
            raise ValueError('Keep the qualified input and MCP ledger in separate files.')

    @contextmanager
    def _database(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.state_path, timeout=15)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS receipts (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, kind, identifier):
        row = db.execute('SELECT body FROM receipts WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _put(db, kind, identifier, value):
        db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)', (kind, identifier, json.dumps(value)))

    @staticmethod
    def _counts(db):
        groups = {}
        for kind, body in db.execute('SELECT kind,body FROM receipts'):
            groups.setdefault(kind, []).append(json.loads(body))
        results = groups.get('result', [])
        return {
            'prepared': len(groups.get('draft', [])), 'held': len(groups.get('held', [])),
            'sent': len(groups.get('send', [])),
            'delivered': sum(bool(r['delivered']) for r in results),
            'bounced': sum(bool(r['bounced']) for r in results),
            'replied': sum(bool(r['replied']) for r in results),
            'meetings': len(groups.get('meeting', [])), 'customers': 0,
        }

    def _answer(self, db, **body):
        return {'mode': 'offline_rehearsal', **body, 'counts': self._counts(db)}

    def _held(self, db, lead_id, *reasons):
        return self._answer(db, ok=False, status='held', lead_id=lead_id, reasons=list(reasons))

    def _lead(self, lead_id):
        try:
            payload = json.loads(self.input_path.read_text())
        except (OSError, ValueError):
            return None, ['Run python3 -m pipeline.run_pipeline --offline first; the qualified input must be readable JSON.']
        if not isinstance(payload, dict) or not isinstance(payload.get('records'), list):
            return None, ['The qualified input needs a records list. Run the earlier stages first.']
        if any(not isinstance(entry, dict) or not isinstance(entry.get('lead', entry), dict)
               for entry in payload['records']):
            return None, ['Every records entry must be a bakery object, with a saved ID.']
        found = [entry for entry in payload['records']
                 if (entry.get('lead', entry)).get('id') == lead_id]
        if len(found) != 1:
            return None, ['This ID must occur exactly once in the qualified list. Rejected and review records stay out.']
        entry = found[0]
        lead = deepcopy(entry.get('lead', entry))
        reasons = ICP().check(lead)
        if entry.get('qualified') is False or entry.get('junk'):
            reasons.append('The record is explicitly marked unqualified.')
        place_id = (lead.get('source') or {}).get('place_id', '')
        designed = (outreach_row_for(lead).get('designed_send') or
                    DESIGNED_SEND_ONCE_A_REVIEW_IS_CLEARED.get(place_id))
        if not outreach_row_for(lead) and not designed:
            reasons.append('This offline exercise has no saved course receipt for that bakery.')
        return lead, reasons

    def prepare_checked_email(self, lead_id: str) -> dict:
        """Check one qualified bakery and return its actual course draft, or hold reasons."""
        with self._database() as db:
            lead, reasons = self._lead(lead_id)
            if reasons:
                return self._held(db, lead_id, *reasons)
            recipient = lead.get('email', '')
            message = BakeryTemplateCopyProvider().write(lead, OFFER)
            problems = lint(message)
            if not isinstance(recipient, str) or not EMAIL.fullmatch(recipient):
                problems.append('A usable saved email address is required.')
            if problems:
                self._put(db, 'held', lead_id, {'lead_id': lead_id, 'reasons': problems})
                return self._answer(db, ok=False, status='held', lead_id=lead_id,
                                    recipient=recipient, message=message, reasons=problems)
            draft_id = 'draft-' + fingerprint([lead_id, recipient, message])[:24]
            draft = {'lead_id': lead_id, 'draft_id': draft_id, 'recipient': recipient,
                     'bakery_name': lead['name'], 'message': message, 'record_hash': fingerprint(lead)}
            self._put(db, 'draft', lead_id, draft)
            db.execute('DELETE FROM receipts WHERE kind=? AND id=?', ('held', lead_id))
            public = {k: v for k, v in draft.items() if k != 'record_hash'}
            return self._answer(db, ok=True, status='checked_draft', **public)

    def send_checked_email(self, lead_id: str, draft_id: str, recipient: str) -> dict:
        """Accept a checked message for fictional delivery; this does not reveal a reply."""
        with self._database() as db:
            lead, reasons = self._lead(lead_id)
            if reasons:
                return self._held(db, lead_id, *reasons)
            draft = self._get(db, 'draft', lead_id)
            if not draft or draft['draft_id'] != draft_id:
                return self._held(db, lead_id, 'Prepare this bakery and use its returned draft_id first.')
            if not recipient or recipient != lead.get('email') or recipient != draft['recipient']:
                return self._held(db, lead_id, 'Use the recipient address on this bakery’s checked draft.')
            if fingerprint(lead) != draft['record_hash']:
                return self._held(db, lead_id, 'The bakery record changed. Prepare and check its draft again.')
            problems = lint(draft['message'])
            if problems:
                return self._held(db, lead_id, *problems)
            previous = self._get(db, 'send', lead_id)
            if previous:
                if previous['draft_id'] != draft_id:
                    return self._held(db, lead_id, 'This bakery already has a sent message in this rehearsal.')
                return self._answer(db, ok=True, already_recorded=True, **previous)
            receipt = {'lead_id': lead_id, 'draft_id': draft_id, 'recipient': recipient,
                       'message_id': 'email-' + fingerprint([lead_id, draft_id])[:24],
                       'status': 'accepted', 'sent': True, 'delivered': None, 'replied': None,
                       'explanation': 'Accepted for delivery. Read the results before assuming arrival or a reply.'}
            self._put(db, 'send', lead_id, receipt)
            self._put(db, 'sent_input', lead_id, {'lead': lead, 'message': draft['message']})
            return self._answer(db, ok=True, already_recorded=False, **receipt)

    def read_outreach_results(self, lead_id: str) -> dict:
        """Read saved delivery/reply evidence for a message already accepted in this run."""
        with self._database() as db:
            sent = self._get(db, 'send', lead_id)
            if not sent:
                return self._held(db, lead_id, 'No message has been sent to this bakery in this rehearsal.')
            result = self._get(db, 'result', lead_id)
            if not result:
                saved = self._get(db, 'sent_input', lead_id)
                if not saved:
                    return self._held(db, lead_id, 'This old rehearsal lacks its original send input. Use a new state file.')
                lead = saved['lead']
                designed = BakeryFixtureSendProvider().send(lead, 'email', saved['message'])
                replies = json.loads(REPLY_FIXTURE.read_text())
                reply = replies.get(lead_id) if designed['replied'] else None
                events = json.loads(MEETING_FIXTURE.read_text())
                event = next((e for e in events if e['lead_id'] == lead_id), None)
                result = {**designed, 'message_id': sent['message_id'], 'bakery_name': lead['name'],
                          'reply_text': (reply or {}).get('reply'),
                          'availability': (reply or {}).get('availability'),
                          'confirmation_evidence': event if designed['replied'] else None,
                          'status': 'replied' if designed['replied'] else ('delivered_no_reply' if designed['delivered'] else 'bounced')}
                self._put(db, 'result', lead_id, result)
            return self._answer(db, ok=True, **result)

    def record_confirmed_meeting(self, lead_id: str, starts_at: str, confirmation: str) -> dict:
        """Record one meeting only after the saved reply and separate confirmation agree."""
        with self._database() as db:
            result = self._get(db, 'result', lead_id)
            if not result or not (result['delivered'] and result['replied']):
                return self._held(db, lead_id, 'Read a delivered, interested reply before recording a meeting.')
            expected = result.get('confirmation_evidence')
            if not expected:
                return self._held(db, lead_id, 'This reply has no agreed time or separate confirmation. Ask about availability next.')
            if starts_at != expected['starts_at'] or confirmation != expected['confirmation']:
                return self._held(db, lead_id, 'The proposed time and confirmation must match the owner’s separate acceptance.')
            accepted = confirmed_meetings({'live': False, 'records': [result]}, [expected])
            if not accepted:
                return self._held(db, lead_id, 'The existing meeting checks rejected this confirmation.')
            previous = self._get(db, 'meeting', lead_id)
            meeting = previous or {**accepted[0], 'player_confirmed': True}
            self._put(db, 'meeting', lead_id, meeting)
            return self._answer(db, ok=True, status='meeting_confirmed', already_recorded=bool(previous), meeting=meeting)
