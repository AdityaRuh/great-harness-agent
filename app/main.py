"""Great Harness Agent — FastAPI application (v1.0 — all phases complete)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.pipelines import router as pipelines_router
from app.api.candidates import router as candidates_router
from app.api.interviews import router as interviews_router
from app.api.interview_eval import router as eval_router
from app.api.offers import router as offers_router
from app.api.audit import router as audit_router
from app.api.careers import router as careers_router
from app.utils.errors import error_handler_middleware
from app.utils.timeout_reminders import start_scheduler, stop_scheduler
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v1.0")
    logger.info(f"Knowledgebase: {settings.knowledgebase_path}")
    logger.info(f"LLM fast: {settings.llm_model_fast} | strong: {settings.llm_model_strong}")
    logger.info(f"Clawvatar: {settings.clawvatar_url}")
    logger.info(f"HR email: {settings.hr_email or '(not set)'}")
    start_scheduler()
    logger.info("Checkpoint reminder scheduler started")
    yield
    stop_scheduler()
    logger.info("Shutting down")


from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(
    title="Great Harness Agent",
    description="AI-powered hiring pipeline — 4 agents, 14 sub-agents, 4 human checkpoints.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(BaseHTTPMiddleware, dispatch=error_handler_middleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(pipelines_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(interviews_router, prefix="/api/v1")
app.include_router(eval_router)
app.include_router(offers_router)
app.include_router(audit_router, prefix="/api/v1")
app.include_router(careers_router)

# Serve static files (avatars, etc)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/dashboard")
async def dashboard():
    """HR Dashboard — full pipeline management UI."""
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health")
async def health():
    clawvatar_status = "not_checked"
    try:
        from app.integrations.clawvatar import ClawvatarClient
        client = ClawvatarClient()
        result = await client.health_check()
        clawvatar_status = result.get("status", "unknown")
    except Exception:
        clawvatar_status = "unreachable"

    from app.utils.timeout_reminders import get_scheduler
    scheduler = get_scheduler()

    return {
        "status": "ok",
        "version": "1.0.0",
        "knowledgebase": settings.knowledgebase_path,
        "clawvatar": clawvatar_status,
        "scheduler": "running" if scheduler.running else "stopped",
        "hr_email": settings.hr_email or "(not configured)",
        "dashboard": "/dashboard",
        "careers": "/careers",
    }




@app.get("/interview/{session_id}")
async def interview_page(session_id: str):
    """Serve the micro1-style interview UI."""
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    settings = get_settings()
    
    template = (Path(__file__).parent / "static" / "interview.html").read_text()
    
    clawvatar_ws = settings.clawvatar_url  # ws://localhost:8765
    clawvatar_http = clawvatar_ws.replace("ws://", "http://").replace("wss://", "https://")
    avatar_url = f"{settings.app_url}/static/avatars/interviewer.vrm"
    
    html = template.replace("__CLAWVATAR_WS__", clawvatar_ws)
    html = html.replace("__CLAWVATAR_HTTP__", clawvatar_http)
    html = html.replace("__AVATAR_URL__", avatar_url)
    html = html.replace("__SESSION_ID__", session_id)
    
    return HTMLResponse(content=html)
