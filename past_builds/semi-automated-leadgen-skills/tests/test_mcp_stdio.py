"""Run actual MCP discovery and calls through local process pipes, not stubs."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip('mcp', reason='Install .[mcp] to test the optional local connection')
from mcp import Client, StdioServerParameters


def test_real_stdio_discovery_validation_results_and_restart(tmp_path, qualified):
    source = tmp_path / 'qualified.json'
    source.write_text(json.dumps(qualified))
    state = tmp_path / 'ledger.sqlite3'
    root = Path(__file__).resolve().parents[1]
    # The server may use process pipes but must never open an Internet socket.
    boot = ('import socket; '
            'socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError("network forbidden")); '
            'socket.create_connection=socket.socket.connect; '
            'from pipeline.outreach_mcp import main; main()')
    server = StdioServerParameters(command=sys.executable,
        args=['-c', boot, '--in', str(source), '--state', str(state)], cwd=str(root))
    sunrise = next(e['lead']['id'] for e in qualified['records'] if e['lead']['name'] == 'Sunrise Bakery')

    async def run():
        async def call(client, name, arguments):
            result = await client.call_tool(name, arguments)
            assert not result.is_error, result.content
            return result.structured_content or json.loads(next(block.text for block in result.content if block.type == 'text'))
        async with Client(server, read_timeout_seconds=15) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                'prepare_checked_email', 'send_checked_email',
                'read_outreach_results', 'record_confirmed_meeting'}
            invalid = await client.call_tool('send_checked_email', {'lead_id': sunrise})
            assert invalid.is_error
            draft = await call(client, 'prepare_checked_email', {'lead_id': sunrise})
            args = {'lead_id': sunrise, 'draft_id': draft['draft_id'], 'recipient': draft['recipient']}
            replies = await asyncio.gather(*(call(client, 'send_checked_email', args) for _ in range(4)))
            assert sum(not r['already_recorded'] for r in replies) == 1
            assert all(r['delivered'] is None for r in replies)
            assert all(r['counts']['sent'] == 1 for r in replies)
            early = await call(client, 'record_confirmed_meeting', {
                'lead_id': sunrise, 'starts_at': '', 'confirmation': ''})
            assert not early['ok']
            result = await call(client, 'read_outreach_results', {'lead_id': sunrise})
            assert result['replied'] and result['counts']['meetings'] == 0
            event = result['confirmation_evidence']
            meeting_args = {'lead_id': sunrise, 'starts_at': event['starts_at'], 'confirmation': event['confirmation']}
            meeting = await call(client, 'record_confirmed_meeting', meeting_args)
            assert meeting['counts']['meetings'] == 1 and meeting['counts']['customers'] == 0
        async with Client(server, read_timeout_seconds=15) as client:
            repeated = await call(client, 'send_checked_email', args)
            assert repeated['already_recorded'] and repeated['counts']['sent'] == 1
            repeated_meeting = await call(client, 'record_confirmed_meeting', meeting_args)
            assert repeated_meeting['already_recorded'] and repeated_meeting['counts']['meetings'] == 1
    asyncio.run(run())


def test_demo_repeats_same_cohort_but_preserves_receipts_for_a_different_list(tmp_path, qualified):
    from pipeline.mcp_demo import run_demo
    source = tmp_path / 'qualified.json'
    source.write_text(json.dumps(qualified))
    out = tmp_path / 'demo'
    first = asyncio.run(run_demo(source, out))
    second = asyncio.run(run_demo(source, out))
    assert first['counts'] == second['counts']
    assert first['counts']['sent'] == 8 and first['counts']['meetings'] == 1
    original_receipts = (out / 'mcp_outreach.json').read_text()
    source.write_text(json.dumps({'records': qualified['records'][:1]}))
    with pytest.raises(ValueError, match='different bakery list'):
        asyncio.run(run_demo(source, out))
    assert (out / 'mcp_outreach.json').read_text() == original_receipts
    independent = asyncio.run(run_demo(source, tmp_path / 'another-demo'))
    assert independent['counts']['sent'] == 1
