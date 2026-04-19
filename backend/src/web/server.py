import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src import state

app = FastAPI(title="Workman API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Vercel URL after deploy
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"ok": True}


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
