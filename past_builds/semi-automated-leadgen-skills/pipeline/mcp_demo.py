"""A real local MCP client talking to a local server, using saved course data.

No model, provider key, network service or email account is used. Start with
python3 -m pipeline.run_pipeline --offline, then run this module.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys


def check_demo_cohort(entries, state_path):
    """Keep a demo's current records and its saved receipt counts comparable."""
    ids = [entry.get('lead', entry).get('id') for entry in entries]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids) or len(set(ids)) != len(ids):
        raise ValueError('The demo needs one saved ID per bakery, without duplicates.')
    signature = json.dumps(sorted(ids))
    with sqlite3.connect(state_path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS demo_cohort (name TEXT PRIMARY KEY, ids TEXT)')
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute("SELECT ids FROM demo_cohort WHERE name='input'").fetchone()
        has_receipts = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipts'").fetchone()
        existing = {row[0] for row in db.execute('SELECT DISTINCT id FROM receipts')} if has_receipts else set()
        if (previous and previous[0] != signature) or not existing.issubset(set(ids)):
            raise ValueError('This ledger belongs to a different bakery list. Use --outdir out/another-mcp-run for a new scenario; the previous receipts are preserved.')
        db.execute("INSERT OR REPLACE INTO demo_cohort VALUES ('input', ?)", (signature,))


async def run_demo(input_path='out/qualified.json', outdir='out'):
    try:
        from mcp import Client, StdioServerParameters
    except ImportError as error:
        raise SystemExit('Install the connection exercise first: python3 -m pip install -e ".[mcp]"') from error
    input_path, outdir = Path(input_path).resolve(), Path(outdir).resolve()
    try:
        entries = json.loads(input_path.read_text())['records']
    except (OSError, ValueError, KeyError) as error:
        raise ValueError('Run python3 -m pipeline.run_pipeline --offline first.') from error
    outdir.mkdir(parents=True, exist_ok=True)
    check_demo_cohort(entries, outdir / 'mcp_outreach.sqlite3')
    trace, results, meetings, held = [], [], [], []
    server = StdioServerParameters(command=sys.executable,
        args=['-m', 'pipeline.outreach_mcp', '--in', str(input_path),
              '--state', str(outdir / 'mcp_outreach.sqlite3')],
        cwd=str(Path(__file__).resolve().parents[1]))
    print('LOCAL MCP REHEARSAL: real tool requests, saved practice results. No model or email is called.')
    async with Client(server, read_timeout_seconds=30) as client:
        available = await client.list_tools()
        print('Available tools: ' + ', '.join(tool.name for tool in available.tools))

        async def call(name, arguments):
            response = await client.call_tool(name, arguments)
            if response.is_error:
                raise RuntimeError(f'The {name} tool rejected its request: {response.content}')
            value = response.structured_content
            if value is None:
                value = json.loads(next(block.text for block in response.content if block.type == 'text'))
            trace.append({'tool': name, 'arguments': arguments, 'result': value})
            return value

        counts = {'sent': 0, 'delivered': 0, 'replied': 0, 'meetings': 0, 'customers': 0}
        for entry in entries:
            lead = entry.get('lead', entry)
            lead_id = lead['id']
            draft = await call('prepare_checked_email', {'lead_id': lead_id})
            if not draft['ok']:
                held.append(draft)
                print(f"HOLD {lead['name']}: {'; '.join(draft['reasons'])}")
                continue
            send = await call('send_checked_email', {'lead_id': lead_id,
                'draft_id': draft['draft_id'], 'recipient': draft['recipient']})
            if not send['ok']:
                raise RuntimeError('; '.join(send['reasons']))
            print(f"SEND {lead['name']}: accepted; delivery and reply not established by this call")
            result = await call('read_outreach_results', {'lead_id': lead_id})
            results.append(result)
            counts = result['counts']
            print(f"RESULT {lead['name']}: {result['status']}")
            confirmation = result.get('confirmation_evidence')
            if confirmation:
                # The demonstration reads a separate authored acceptance. A
                # learner using Claude should inspect it before this action.
                meeting = await call('record_confirmed_meeting', {'lead_id': lead_id,
                    'starts_at': confirmation['starts_at'], 'confirmation': confirmation['confirmation']})
                if not meeting['ok']:
                    raise RuntimeError('; '.join(meeting['reasons']))
                meetings.append(meeting['meeting'])
                counts = meeting['counts']
                print('CONFIRMATION: ' + confirmation['confirmation'])
        # Read the last result again to collect final counters after all sends.
        if results:
            last = await call('read_outreach_results', {'lead_id': results[-1]['lead_id']})
            counts = last['counts']
    payload = {'mode': 'offline_mcp_rehearsal', 'live': False, 'counts': counts,
               'records': results, 'held_records': held, 'meetings': meetings}
    (outdir / 'mcp_tool_trace.json').write_text(json.dumps(trace, indent=2) + '\n')
    (outdir / 'mcp_outreach.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(f"{counts['sent']} sent · {counts['delivered']} delivered · {counts['replied']} replies · {counts['meetings']} confirmed meeting · {counts['customers']} customers")
    print('Saved mcp_tool_trace.json and mcp_outreach.json. Repeating a request does not duplicate a send or meeting.')
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in', dest='src', default='out/qualified.json')
    parser.add_argument('--outdir', default='out')
    args = parser.parse_args(argv)
    try:
        asyncio.run(run_demo(args.src, args.outdir))
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
