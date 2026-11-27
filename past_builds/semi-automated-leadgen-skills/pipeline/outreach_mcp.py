"""Expose the four Outreach actions through a real local MCP connection.

Run from the repository root: python3 -m pipeline.outreach_mcp
This is a stdio server: its standard input/output belong to the MCP client.
"""
from __future__ import annotations
import argparse

from .outreach_tools import OutreachTools


def make_server(input_path='out/qualified.json', state_path='out/mcp_outreach.sqlite3'):
    try:
        from mcp.server import MCPServer
    except ImportError as error:
        raise SystemExit('Install the connection exercise first: python3 -m pip install -e ".[mcp]"') from error
    actions = OutreachTools(input_path, state_path)
    server = MCPServer('course-outreach', version='1.0.0', instructions=(
        'Local fictional course rehearsal only. Prepare a draft, send it, read the result, '
        'then record only a separately confirmed meeting. Rejected or unknown records stay out. '
        'Sending is not delivery; a reply is not a booking; a meeting is not a customer.'))
    server.tool()(actions.prepare_checked_email)
    server.tool()(actions.send_checked_email)
    server.tool()(actions.read_outreach_results)
    server.tool()(actions.record_confirmed_meeting)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in', dest='src', default='out/qualified.json')
    parser.add_argument('--state', default='out/mcp_outreach.sqlite3')
    args = parser.parse_args(argv)
    make_server(args.src, args.state).run(transport='stdio')


if __name__ == '__main__':
    main()
