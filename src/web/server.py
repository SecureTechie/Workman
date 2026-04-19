import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from src import state

app = FastAPI(title="Workman")

_STATIC = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (_STATIC / "index.html").read_text()


@app.get("/api/status")
async def api_status():
    return {"issues": state.get_all(), "steps": state.STEPS}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    state.register_ws(websocket)
    try:
        # Send current snapshot on connect
        await websocket.send_text(json.dumps({
            "type": "init",
            "issues": state.get_all(),
            "steps": state.STEPS,
        }))
        # Keep alive — the broadcaster pushes all updates, we just wait
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.unregister_ws(websocket)
