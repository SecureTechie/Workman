import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
WORKDIR = os.getenv("WORKDIR", "/tmp/workman")

# Comma-separated GitHub org names to watch for assigned issues.
# e.g. "stellar,drips-network" — leave empty to watch all orgs (use with care).
WATCH_ORGS = os.getenv("WATCH_ORGS", "")

MAX_SOLVER_ITERATIONS = 40
