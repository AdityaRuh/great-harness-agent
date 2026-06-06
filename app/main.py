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
    strong = settings.openai_codex_model_strong or settings.openai_codex_model
    logger.info(f"LLM (OpenAI Codex): {settings.openai_codex_model} | strong: {strong}")
    logger.info(f"Clawvatar: {settings.clawvatar_url}")
    logger.info(f"HR email: {settings.hr_email or '(not set)'}")

    # Initialize storage (PostgreSQL or in-memory fallback)
    try:
        from app.storage import init_storage
        await init_storage()
    except Exception as e:
        logger.warning(f"Storage init failed: {e} — using in-memory")

    # Initialize graph checkpointer (async)
    try:
        import os, re
        db_url = os.environ.get("DATABASE_URL", settings.database_url)
        if db_url and db_url.startswith("postgresql") and "user:pass@localhost" not in db_url:
            sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
            sync_url = re.sub(r'[&?]channel_binding=[^&]*', '', sync_url)
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            import psycopg
            # Setup tables
            try:
                from langgraph.checkpoint.postgres import PostgresSaver as SyncSaver
                with psycopg.connect(sync_url, autocommit=True) as sc:
                    SyncSaver(sc).setup()
            except Exception as se:
                if "already exists" not in str(se) and "duplicate key" not in str(se):
                    logger.warning(f"Checkpoint table setup: {se}")
            # Create and open async pool
            pool = AsyncConnectionPool(conninfo=sync_url, min_size=1, max_size=3)
            await pool.open()
            app.state.checkpointer = AsyncPostgresSaver(pool)
            app.state.checkpoint_pool = pool
            logger.info("AsyncPostgresSaver initialized with open pool")
    except Exception as e:
        logger.warning(f"AsyncPostgresSaver init failed: {e}")

    start_scheduler()
    logger.info("Checkpoint reminder scheduler started")
    yield

    # Cleanup
    try:
        if hasattr(app.state, "checkpoint_pool"):
            await app.state.checkpoint_pool.close()
        from app.db import close_db
        await close_db()
    except Exception:
        pass
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
    """Serve the interview UI — or 'completed' page if already done."""
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    from app.api.interview_eval import _interview_results
    settings = get_settings()

    # Check if interview already completed
    if session_id in _interview_results:
        result = _interview_results[session_id]
        return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Interview Complete — Ruh AI</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'DM Sans',-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;height:100vh;display:flex;align-items:center;justify-content:center}}
.card{{max-width:500px;text-align:center;padding:48px;background:#12151B;border:1px solid #1E2230;border-radius:16px}}
.icon{{font-size:64px;margin-bottom:24px}}
h1{{font-size:24px;margin-bottom:12px;color:#4ade80}}
p{{color:#6B7394;font-size:14px;line-height:1.6;margin-bottom:16px}}
.score{{font-size:48px;font-weight:700;font-family:'JetBrains Mono',monospace;color:#7c6ef0;margin:16px 0}}
.verdict{{display:inline-block;padding:4px 16px;border-radius:20px;font-size:13px;font-weight:600;
  background:rgba(124,110,240,0.1);border:1px solid rgba(124,110,240,0.3);color:#a29bfe}}
</style></head><body>
<div class="card">
  <div class="icon">✅</div>
  <h1>Interview Complete</h1>
  <p>Thank you, <strong>{result.get('candidate_name', 'Candidate')}</strong>! Your interview has already been submitted and evaluated.</p>
  <div class="score">{result.get('interview_score', 'N/A')}/100</div>
  <div class="verdict">{result.get('verdict', 'Evaluated')}</div>
  <p style="margin-top:24px">You will receive detailed results via email within 24 hours. If you have questions, please contact <strong>hr@ruh.ai</strong>.</p>
</div></body></html>""")

    template = (Path(__file__).parent / "static" / "interview.html").read_text()

    clawvatar_ws = settings.clawvatar_url
    clawvatar_http = clawvatar_ws.replace("ws://", "http://").replace("wss://", "https://")
    avatar_url = f"{settings.app_url}/static/avatars/interviewer.vrm"

    html = template.replace("__CLAWVATAR_WS__", clawvatar_ws)
    html = html.replace("__CLAWVATAR_HTTP__", clawvatar_http)
    html = html.replace("__AVATAR_URL__", avatar_url)
    html = html.replace("__SESSION_ID__", session_id)

    return HTMLResponse(content=html)
