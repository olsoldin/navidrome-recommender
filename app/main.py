import asyncio
import functools
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_settings
from .navidrome_client import SubsonicClient, SubsonicError
from .recommend import RecommendationEngine

app = FastAPI(title="Navidrome Recommender")

jobs: Dict[str, Dict[str, Any]] = {}
engine = RecommendationEngine()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ScanRequest(BaseModel):
    genre_weights: Optional[Dict[str, float]] = None


@app.get("/api/health")
def health():
    settings = get_settings()
    return {"configured": settings.is_configured, "navidrome_url": settings.navidrome_url or None}


@app.post("/api/test-connection")
def test_connection():
    settings = get_settings()
    if not settings.is_configured:
        raise HTTPException(400, "Server not configured. Set NAVIDROME_URL, NAVIDROME_USER, and NAVIDROME_PASS in .env, then restart the app.")
    client = SubsonicClient(settings.navidrome_url, settings.navidrome_user, settings.navidrome_pass)
    try:
        client.ping()
        return {"ok": True}
    except SubsonicError as e:
        raise HTTPException(400, f"Navidrome rejected the request: {e}")
    except Exception as e:
        raise HTTPException(400, f"Could not reach {settings.navidrome_url}: {e}")


@app.post("/api/scan")
async def start_scan(req: ScanRequest = Body(default=ScanRequest())):
    settings = get_settings()
    if not settings.is_configured:
        raise HTTPException(400, "Server not configured. Set NAVIDROME_URL, NAVIDROME_USER, and NAVIDROME_PASS in .env, then restart the app.")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "running", "message": "Starting up...", "result": None, "error": None}
    asyncio.create_task(_run_scan(job_id, settings, req.genre_weights))
    return {"job_id": job_id}


async def _run_scan(job_id: str, settings, genre_weights: Optional[Dict[str, float]] = None) -> None:
    def progress(message: str) -> None:
        jobs[job_id]["message"] = message

    loop = asyncio.get_event_loop()
    try:
        client = SubsonicClient(settings.navidrome_url, settings.navidrome_user, settings.navidrome_pass)
        task = functools.partial(engine.build_recommendations, client, progress, genre_weights=genre_weights)
        result = await loop.run_in_executor(None, task)
        jobs[job_id]["status"] = "done"
        jobs[job_id]["result"] = result
    except SubsonicError as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = f"Navidrome error: {e}"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.get("/api/scan/{job_id}")
def get_scan(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# Serve the dashboard itself. Mounted last so it doesn't shadow /api routes.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
