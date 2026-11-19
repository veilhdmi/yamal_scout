"""Web app for Yamal Scout: dashboard API + streaming chat (Server-Sent Events) + the static front end.

Run:  python server.py            (this PC only, http://localhost:8000)
      python server.py --lan      (also reachable from other devices on your Wi-Fi)
"""

import argparse
import json
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import dashboard
from agent import chat_stream

WEB = Path(__file__).parent / "web"
MAX_SESSIONS = 50



@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fitting four models + backtests takes a few seconds: do it before the first visitor asks.
    threading.Thread(target=lambda: (dashboard.overview(), dashboard.forecast()), daemon=True).start()
    yield


app = FastAPI(title="Yamal Scout", lifespan=lifespan)
sessions: dict[str, list] = {}  # session id -> full API transcript (in memory: a restart starts fresh)
busy: set[str] = set()
sessions_lock = threading.Lock()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)


@app.get("/api/overview")
def api_overview() -> dict:
    return dashboard.overview()


@app.get("/api/fixtures")
def api_fixtures() -> dict:
    return dashboard.fixtures()


@app.get("/api/forecast")
def api_forecast() -> dict:
    return dashboard.forecast()


@app.get("/api/news")
def api_news() -> dict:
    return dashboard.news()


@app.post("/api/refresh")
def api_refresh() -> dict:
    dashboard.refresh()
    return {"ok": True}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/chat")
def api_chat(req: ChatRequest) -> StreamingResponse:
    with sessions_lock:
        if req.session_id in busy:
            raise HTTPException(409, "Still answering the previous question.")
        if req.session_id not in sessions and len(sessions) >= MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))  # drop the oldest conversation
        history = sessions.setdefault(req.session_id, [])
        busy.add(req.session_id)

    def events():
        checkpoint = len(history)
        history.append({"role": "user", "content": req.message})
        try:
            for event in chat_stream(history):
                yield _sse(event)
        except anthropic.APIStatusError as exc:
            del history[checkpoint:]  # never keep a half-finished turn (e.g. tool_use without its result)
            yield _sse({"type": "error", "message": f"Claude API error {exc.status_code}. Try again in a moment."})
        except anthropic.APIConnectionError:
            del history[checkpoint:]
            yield _sse({"type": "error", "message": "Could not reach the Claude API. Check your connection."})
        except Exception as exc:  # BigQuery, ESPN, etc.
            del history[checkpoint:]
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            with sessions_lock:
                busy.discard(req.session_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/reset")
def api_reset(req: ResetRequest) -> dict:
    with sessions_lock:
        if req.session_id in busy:
            raise HTTPException(409, "Wait until the current answer finishes.")
        sessions.pop(req.session_id, None)
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


def _lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # no packet is sent; this just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan", action="store_true", help="listen on the local network too")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"\n  Yamal Scout -> http://localhost:{args.port}")
    if args.lan:
        print(f"  On your Wi-Fi  -> http://{_lan_ip()}:{args.port}  (anyone on this network can use it)\n")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")
