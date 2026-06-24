"""Shared paths for all claude-usage-meter components."""
import os

DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "claude-usage-meter",
)
os.makedirs(DATA_DIR, exist_ok=True)

ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
USAGE_FILE    = os.path.join(DATA_DIR, "usage.json")
LOG_FILE      = os.path.join(DATA_DIR, "poller.log")
LEDGER_DB     = os.path.join(DATA_DIR, "usage_history.db")
