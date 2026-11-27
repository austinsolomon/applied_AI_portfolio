"""Configure this project's local MCP server; no model or server is started."""
import json
from pathlib import Path
import sys


def main():
    try:
        from mcp.server import MCPServer  # noqa: F401
    except ImportError as error:
        raise SystemExit('Install the exercise first: python3 -m pip install -e ".[mcp]"') from error
    root = Path(__file__).resolve().parents[1]
    path = root / '.mcp.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault('mcpServers', {})['course-outreach'] = {
        'type': 'stdio', 'command': sys.executable,
        'args': ['-m', 'pipeline.outreach_mcp', '--in', str(root / 'out/qualified.json'),
                 '--state', str(root / 'out/mcp_outreach.sqlite3')],
    }
    path.write_text(json.dumps(data, indent=2) + '\n')
    print(f'Configured the local course-outreach tools in {path}.')
    print('Open Claude Code here, review the local project connection, then use /mcp to check it.')
    print('No model was called and no email was sent. Claude uses your own account.')


if __name__ == '__main__':
    main()
