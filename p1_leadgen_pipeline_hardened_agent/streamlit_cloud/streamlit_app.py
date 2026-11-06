"""Streamlit Community Cloud entrypoint: the read-only hosted dashboard.

Community Cloud runs this file from the repository root and installs the requirements.txt beside
it. Settings: Python 3.12, and Secrets (TOML):

- Public or shared app: DATABASE_URL = the DASHBOARD_DATABASE_URL secret (read-only views). Shows
  demand gaps, run history and sources only.
- Private app (viewers invited by email): DATABASE_URL = the OPERATOR_DATABASE_URL secret, plus
  DASHBOARD_MODE = "operator". Shows every tab, including evidence, contacts and eval labeling.

Without DASHBOARD_MODE = "operator" the page stays in the safe read-only mode.
"""

import os
import runpy
import sys
from pathlib import Path

import streamlit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))
# Category config and other relative paths resolve from the project folder.
os.chdir(PROJECT)
mode = "hosted"
try:
    if "DATABASE_URL" in streamlit.secrets:
        os.environ["DATABASE_URL"] = streamlit.secrets["DATABASE_URL"]
    if streamlit.secrets.get("DASHBOARD_MODE") == "operator":
        mode = "operator"
except FileNotFoundError:
    pass  # No secrets file: fall back to an exported DATABASE_URL, read-only mode.
os.environ["LEADGEN_DASHBOARD_MODE"] = mode

# Run the page on every rerun; a plain import would render only once per process.
runpy.run_path(str(PROJECT / "src" / "leadgen" / "dashboard.py"), run_name="__main__")
