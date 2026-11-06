"""Streamlit Community Cloud entrypoint: the read-only hosted dashboard.

Community Cloud runs this file from the repository root and installs the requirements.txt beside
it. Settings: Python 3.12; Secrets (TOML) holds one line, DATABASE_URL = "<the read-only
leadgen_dashboard connection string>", from the DASHBOARD_DATABASE_URL secret in Secret Manager.
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
os.environ["LEADGEN_DASHBOARD_MODE"] = "hosted"
try:
    if "DATABASE_URL" in streamlit.secrets:
        os.environ["DATABASE_URL"] = streamlit.secrets["DATABASE_URL"]
except FileNotFoundError:
    pass  # No secrets file: fall back to an exported DATABASE_URL.

# Run the page on every rerun; a plain import would render only once per process.
runpy.run_path(str(PROJECT / "src" / "leadgen" / "dashboard.py"), run_name="__main__")
