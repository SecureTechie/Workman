"""
Workman — autonomous Drips Wave issue solver with live dashboard.

Usage:
    python main.py          # Start web server + polling loop
    python main.py --once   # One poll cycle then exit (no web server)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn

import config
from src import state
from src.drips.watcher import DripsWatcher
from src.github.client import GitHubClient
from src.pipeline import run_pipeline
from src.web.server import app

# ---------------- LOGGING ---------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("workman.log"),
    ],
)
logger = logging.getLogger("workman")

STATE_FILE = Path("state.json")
MAX_RETRIES = 3


# ---------------- STATE ---------------- #

def load_processed() -> tuple[set[str], dict[str, int]]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if not isinstance(data, dict):
                raise ValueError("State file is not a JSON object")
            return set(data.get("processed", [])), data.get("failures", {})
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"Corrupted {STATE_FILE}, starting fresh")
    return set(), {}


def save_processed(processed: set[str], failures: dict[str, int]) -> None:
    STATE_FILE.write_text(
        json.dumps({"processed": list(processed), "failures": failures}, indent=2)
    )


# ---------------- POLL LOOP ---------------- #

async def poll_loop(
    watcher: DripsWatcher, processed: set[str], failures: dict[str, int]
) -> None:
    while True:
        await check_and_process(watcher, processed, failures)
        logger.info(f"Sleeping {config.POLL_INTERVAL}s until next check...")
        await asyncio.sleep(config.POLL_INTERVAL)


async def check_and_process(
    watcher: DripsWatcher, processed: set[str], failures: dict[str, int]
) -> None:
    logger.info("Checking Drips for assigned issues...")
    state.log(None, "Polling Drips for assigned issues...")

    try:
        issues = await watcher.get_assigned_issues()
    except Exception as e:
        msg = f"Failed to fetch issues: {e}"
        logger.error(msg)
        state.log(None, f"ERROR: {msg}")
        return

    candidates = [
        i
        for i in issues
        if i.id not in processed and failures.get(i.id, 0) < MAX_RETRIES
    ]

    if not candidates:
        logger.info("No new assigned issues.")
        state.log(None, "No new assigned issues found.")
        return

    gh = GitHubClient()
    actionable = []

    for issue in candidates:
        try:
            pr_url = await asyncio.to_thread(
                gh.find_existing_pr,
                issue.repo_owner,
                issue.repo_name,
                issue.issue_number,
            )
        except Exception:
            pr_url = None

        if pr_url:
            logger.info(f"Skipping {issue.id} — PR exists")
            processed.add(issue.id)
            save_processed(processed, failures)
        else:
            actionable.append(issue)

    for issue in actionable:
        logger.info(f"Processing {issue.id}...")
        try:
            pr_url = await asyncio.to_thread(run_pipeline, issue)
            logger.info(f"SUCCESS — PR: {pr_url}")
            processed.add(issue.id)
            failures.pop(issue.id, None)
            save_processed(processed, failures)
        except Exception as e:
            failures[issue.id] = failures.get(issue.id, 0) + 1
            save_processed(processed, failures)
            logger.error(f"Pipeline failed for {issue.id}: {e}", exc_info=True)


# ---------------- MAIN ---------------- #

async def main_async(once: bool) -> None:
    _validate_config()

    loop = asyncio.get_event_loop()
    state.init(loop)

    watcher = DripsWatcher()
    processed, failures = load_processed()

    if once:
        await check_and_process(watcher, processed, failures)
        return

    # 🔥 CRITICAL FOR RENDER
    port = int(os.environ.get("PORT", 8000))

    config_uv = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config_uv)

    await asyncio.gather(
        server.serve(),
        poll_loop(watcher, processed, failures),
    )


# ---------------- VALIDATION ---------------- #

def _validate_config() -> None:
    missing = [
        k
        for k, v in [("GITHUB_TOKEN", config.GITHUB_TOKEN), ("GITHUB_USERNAME", config.GITHUB_USERNAME)]
        if not v
    ]
    provider_key = {
        "gemini":    ("GEMINI_API_KEY",    config.GEMINI_API_KEY),
        "openai":    ("OPENAI_API_KEY",    config.OPENAI_API_KEY),
        "anthropic": ("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY),
    }.get(config.AI_PROVIDER)
    if provider_key is None:
        logger.error(f"Unknown AI_PROVIDER: '{config.AI_PROVIDER}'. Must be gemini, openai, or anthropic.")
        sys.exit(1)
    if not provider_key[1]:
        missing.append(provider_key[0])
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    if not config.DASHBOARD_TOKEN:
        logger.warning("DASHBOARD_TOKEN is not set — dashboard is unsecured")


# ---------------- ENTRY ---------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(once=args.once))
    except Exception as e:
        import traceback
        print("STARTUP CRASH:", repr(e))
        traceback.print_exc()
        raise