import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DRIPS_COOKIES_FILE = os.getenv("DRIPS_COOKIES_FILE", "cookies.json")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
WORKDIR = os.getenv("WORKDIR", "/tmp/workman")

DRIPS_BASE_URL = "https://www.drips.network"
DRIPS_ISSUES_URL = f"{DRIPS_BASE_URL}/wave/contributors/issues"

MAX_SOLVER_ITERATIONS = 40
