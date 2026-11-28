"""Shared pytest setup for the GTM agent's tests.

The agent's modules (slack_events.py, system.py, …) live one folder up, in gtm_agent/, and are
imported by name, so that folder goes on the import path once, here, for every test file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
