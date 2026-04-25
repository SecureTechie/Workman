import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import config
from src import state

logger = logging.getLogger("workman.web")

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
