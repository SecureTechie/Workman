import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from src import state

logger = logging.getLogger("workman.web")


class RetryTaskRequest(BaseModel):
    repo: str        # "owner/repo"
    issue_number: int

_LOG_RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
}

app = FastAPI(title="Workman API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "x-token"],
)


def _resolve_token(request: Request, token: str = "") -> str:
    """Extract token from ?token= query param or x-token header."""
    return token or request.headers.get("x-token", "")


def _check_token(route: str, token: str) -> None:
    """Validate token if DASHBOARD_TOKEN is set. Raises 403 on failure."""
    logger.info("Request received: %s | token present: %s", route, bool(token))
    if not config.DASHBOARD_TOKEN:
        return
    if token != config.DASHBOARD_TOKEN:
        logger.warning("Request rejected: %s | token invalid", route)
        raise HTTPException(status_code=403, detail="Unauthorized")
    logger.info("Request allowed: %s", route)


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    return {"ok": True}


@app.get("/api/status")
async def api_status(request: Request, token: str = Query(default="")):
    _check_token("/api/status", _resolve_token(request, token))
    return {"issues": state.get_all(), "steps": state.STEPS}


@app.get("/api/logs")
async def api_logs(request: Request, range: str = Query("1h"), token: str = Query(default="")):
    _check_token("/api/logs", _resolve_token(request, token))
    if range not in _LOG_RANGES:
        raise HTTPException(status_code=400, detail=f"range must be one of: {', '.join(_LOG_RANGES)}")
    since = (datetime.now(timezone.utc) - _LOG_RANGES[range]).isoformat()
    return {"logs": state.get_logs_since(since), "range": range}


@app.post("/api/control/skip-current")
async def control_skip(request: Request, token: str = Query(default="")):
    _check_token("/api/control/skip-current", _resolve_token(request, token))
    logger.info("User requested skip current task")
    state.log(None, "User requested skip current task")
    state.request_skip()
    return {"ok": True, "action": "skip-current"}


@app.post("/api/control/pause")
async def control_pause(request: Request, token: str = Query(default="")):
    _check_token("/api/control/pause", _resolve_token(request, token))
    logger.info("Bot paused")
    state.log(None, "Bot paused")
    state.set_paused(True)
    return {"ok": True, "action": "pause"}


@app.post("/api/control/resume")
async def control_resume(request: Request, token: str = Query(default="")):
    _check_token("/api/control/resume", _resolve_token(request, token))
    logger.info("Bot resumed")
    state.log(None, "Bot resumed")
    state.set_paused(False)
    return {"ok": True, "action": "resume"}


@app.get("/api/issues/queue")
async def issues_queue(request: Request, token: str = Query(default=""),
                       include_done: bool = Query(default=False)):
    _check_token("/api/issues/queue", _resolve_token(request, token))
    import main as _main
    from src.drips.watcher import DripsWatcher
    from src.solver.classifier import classify

    # --- 1. Fetch all currently assigned issues from GitHub ---
    try:
        watcher = DripsWatcher()
        gh_issues = await asyncio.to_thread(watcher._fetch)
    except Exception as e:
        logger.warning(f"Queue: GitHub fetch failed ({e}), falling back to in-memory state")
        gh_issues = []

    # --- 2. Build a lookup of in-memory pipeline state ---
    current_id   = state.get_current_issue()
    state_issues = {i["id"]: i for i in state.get_all()}
    now          = datetime.now(timezone.utc)
    STALL_MINS   = 20

    # Pipeline steps in order, used for progress calculation
    ORDERED_STEPS = ["detected", "fetching", "forking", "cloning",
                     "setup", "solving", "pushing", "done"]

    # --- 3. Merge: start from GitHub truth, enrich with pipeline state ---
    seen: set[str] = set()
    rows = []

    for gh in gh_issues:
        iid = gh.id
        seen.add(iid)
        s_issue  = state_issues.get(iid, {})
        meta     = state.get_queue_meta(iid)
        failures = _main._failures.get(iid, 0)
        in_proc  = _main._processed

        # Determine status
        if iid == current_id:
            status = "processing"
        elif s_issue.get("step") == "done" or iid in in_proc and s_issue.get("step") == "done":
            status = "done"
        elif s_issue.get("failed") and failures >= _main.MAX_RETRIES:
            status = "skipped"
        elif s_issue.get("step") == "skipped":
            status = "skipped"
        elif iid in in_proc:
            status = "done"  # processed and not failed = done
        elif meta.get("status") in ("failed", "skipped"):
            status = meta["status"]
        else:
            status = "queued"

        if status == "done" and not include_done:
            continue

        # Progress
        current_step = s_issue.get("step") or "queued"
        try:
            step_idx = ORDERED_STEPS.index(current_step)
            progress_percent = round((step_idx / (len(ORDERED_STEPS) - 1)) * 100)
        except ValueError:
            progress_percent = 0

        # Stall detection: processing but updated_at hasn't moved in STALL_MINS
        stalled = False
        updated_at = s_issue.get("updated_at")
        if status == "processing" and updated_at:
            try:
                last = datetime.fromisoformat(updated_at)
                if (now - last).total_seconds() > STALL_MINS * 60:
                    stalled = True
                    logger.warning(f"Issue appears stalled — manual review recommended: {iid}")
                    state.log(iid, "Issue appears stalled — manual review recommended")
            except Exception:
                pass

        # Difficulty
        difficulty = meta.get("difficulty")
        if not difficulty:
            try:
                difficulty = classify(gh)
            except Exception:
                difficulty = "UNKNOWN"
        score = {"EASY": 0, "MEDIUM": 1, "HARD": 2}.get(difficulty, 99)

        if status == "processing":
            logger.info(f"Current issue progress: {gh.repo_owner}/{gh.repo_name}#{gh.issue_number} - {current_step}")

        rows.append({
            "id":               iid,
            "repo":             f"{gh.repo_owner}/{gh.repo_name}",
            "issue_number":     gh.issue_number,
            "title":            gh.title or s_issue.get("title") or iid,
            "url":              gh.github_issue_url,
            "difficulty":       difficulty,
            "score":            score,
            "status":           status,
            "current_step":     current_step,
            "progress_percent": progress_percent,
            "started_at":       s_issue.get("started_at"),
            "updated_at":       updated_at,
            "reason":           meta.get("reason"),
            "failures":         failures,
            "priority":         state.is_priority(iid),
            "is_current":       iid == current_id,
            "stalled":          stalled,
        })

    # Also include any in-memory issues not returned by GitHub (e.g. closed/unassigned)
    for iid, s_issue in state_issues.items():
        if iid in seen:
            continue
        meta     = state.get_queue_meta(iid)
        status   = meta.get("status") or s_issue.get("step", "queued")
        failures = _main._failures.get(iid, 0)
        if status == "done" and not include_done:
            continue
        current_step = s_issue.get("step", "queued")
        try:
            step_idx = ORDERED_STEPS.index(current_step)
            progress_percent = round((step_idx / (len(ORDERED_STEPS) - 1)) * 100)
        except ValueError:
            progress_percent = 0
        # Parse repo/number from id format "owner/repo#number"
        repo = "/".join(iid.split("/")[:2]) if "/" in iid else iid
        try:
            issue_number = int(iid.split("#")[1]) if "#" in iid else None
        except (IndexError, ValueError):
            issue_number = None
        rows.append({
            "id":               iid,
            "repo":             repo,
            "issue_number":     issue_number,
            "title":            s_issue.get("title", iid),
            "url":              s_issue.get("github_issue_url"),
            "difficulty":       meta.get("difficulty", "UNKNOWN"),
            "score":            meta.get("score", 99),
            "status":           status,
            "current_step":     current_step,
            "progress_percent": progress_percent,
            "started_at":       s_issue.get("started_at"),
            "updated_at":       s_issue.get("updated_at"),
            "reason":           meta.get("reason"),
            "failures":         failures,
            "priority":         state.is_priority(iid),
            "is_current":       iid == current_id,
            "stalled":          False,
        })

    # --- 4. Sort ---
    def _rank(r):
        s = r["status"]
        if r["is_current"]:                                    return (0, 0)
        if r["priority"]:                                      return (1, r["score"])
        if s == "queued" and r["score"] == 0:                  return (2, 0)
        if s == "queued" and r["score"] == 1:                  return (3, 0)
        if s in ("failed", "queued") and r["failures"] < 3:   return (4, r["failures"])
        if s == "skipped" and r["difficulty"] == "HARD":       return (5, 0)
        if s == "skipped":                                     return (6, 0)
        return (7, 0)

    rows.sort(key=_rank)
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    logger.info(f"Queue generated with {len(rows)} assigned issues")
    return {"queue": rows}


@app.post("/api/control/retry-task")
async def control_retry_task(body: RetryTaskRequest, request: Request,
                             token: str = Query(default="")):
    _check_token("/api/control/retry-task", _resolve_token(request, token))
    import main as _main
    # Reconstruct the canonical issue id used throughout the system
    issue_id = f"{body.repo}#{body.issue_number}"
    logger.info(f"User prioritized task {body.repo}#{body.issue_number}")
    state.log(None, f"User prioritized task {body.repo}#{body.issue_number}")
    # Remove from processed so the poll loop picks it up again
    _main._processed.discard(issue_id)
    _main._failures.pop(issue_id, None)
    _main.save_processed(_main._processed, _main._failures)
    state.set_priority(issue_id, True)
    state.upsert_issue(issue_id, step="queued", failed=False, error=None)
    state.upsert_queue_meta(issue_id, status="queued", reason=None)
    return {"ok": True, "id": issue_id}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(default="")):
    resolved = token or websocket.headers.get("x-token", "")
    logger.info("WebSocket request received | token present: %s", bool(resolved))
    await websocket.accept()
    if config.DASHBOARD_TOKEN and resolved != config.DASHBOARD_TOKEN:
        logger.warning("WebSocket rejected | token invalid")
        await websocket.close(code=1008, reason="Unauthorized")
        return
    logger.info("WebSocket allowed")

    state.register_ws(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "init",
            "issues": state.get_all(),
            "steps": state.STEPS,
        }))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.unregister_ws(websocket)
