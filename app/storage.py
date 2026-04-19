"""Storage abstraction — PostgreSQL with in-memory fallback.

All API modules import from here instead of managing their own dicts.
If PostgreSQL is configured, data persists across restarts.
If not, works identically to before using in-memory dicts.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_use_db = False

# In-memory fallback stores (same as before)
_mem_pipelines: dict[str, dict] = {}
_mem_hr_decisions: dict[str, dict] = {}
_mem_interview_results: dict[str, dict] = {}
_mem_interview_questions: dict[str, list] = {}
_mem_interview_question_meta: dict[str, dict] = {}
_mem_interview_hr_decisions: dict[str, dict] = {}
_mem_scheduled: dict[str, dict] = {}
_mem_applications: dict[str, list] = {}
_mem_shortlist_approved: set = set()
_mem_running_pipelines: set = set()


async def init_storage():
    """Try to connect to PostgreSQL. Falls back to in-memory if unavailable."""
    global _use_db
    try:
        from app.db import init_db
        await init_db()
        _use_db = True
        logger.info("Storage: PostgreSQL active")
    except Exception as e:
        _use_db = False
        logger.warning(f"Storage: In-memory mode (PostgreSQL unavailable: {e})")


def is_db_active() -> bool:
    return _use_db


# ── Pipeline Storage ─────────────────────────────────────────────

async def save_pipeline(pipeline_id: str, config: dict, status: str = "unknown"):
    _mem_pipelines[pipeline_id] = {"id": pipeline_id, "config": config, "last_status": status, "last_state_cache": {}}
    if _use_db:
        try:
            from app.db import save_pipeline as db_save
            await db_save(pipeline_id, config, status)
        except Exception as e:
            logger.warning(f"DB save_pipeline failed: {e}")


async def get_pipeline(pipeline_id: str) -> Optional[dict]:
    if pipeline_id in _mem_pipelines:
        return _mem_pipelines[pipeline_id]
    if _use_db:
        try:
            from app.db import get_pipeline_data
            return await get_pipeline_data(pipeline_id)
        except Exception:
            pass
    return None


def get_pipeline_sync(pipeline_id: str) -> Optional[dict]:
    """Sync access to pipeline data (for in-process use only)."""
    return _mem_pipelines.get(pipeline_id)


async def list_all_pipelines() -> dict:
    """Returns the full pipeline dict."""
    return _mem_pipelines


async def update_pipeline_cache(pipeline_id: str, status: str, state_cache: dict):
    if pipeline_id in _mem_pipelines:
        _mem_pipelines[pipeline_id]["last_status"] = status
        _mem_pipelines[pipeline_id]["last_state_cache"] = state_cache
    if _use_db:
        try:
            from app.db import update_pipeline_cache as db_update
            await db_update(pipeline_id, status, state_cache)
        except Exception as e:
            logger.warning(f"DB update_pipeline_cache failed: {e}")


# Running pipeline tracking (always in-memory since it's transient)
def mark_running(pipeline_id: str):
    _mem_running_pipelines.add(pipeline_id)

def mark_done(pipeline_id: str):
    _mem_running_pipelines.discard(pipeline_id)

def is_running(pipeline_id: str) -> bool:
    return pipeline_id in _mem_running_pipelines


# ── HR Decisions ─────────────────────────────────────────────────

async def save_hr_decision(pipeline_id: str, candidate_key: str, decision: str, note: str = ""):
    _mem_hr_decisions.setdefault(pipeline_id, {})[candidate_key] = {"decision": decision, "note": note}
    if _use_db:
        try:
            from app.db import save_hr_decision as db_save
            await db_save(pipeline_id, candidate_key, decision, note)
        except Exception as e:
            logger.warning(f"DB save_hr_decision failed: {e}")


async def get_hr_decisions(pipeline_id: str) -> dict:
    if pipeline_id in _mem_hr_decisions:
        return _mem_hr_decisions[pipeline_id]
    if _use_db:
        try:
            from app.db import get_hr_decisions as db_get
            return await db_get(pipeline_id)
        except Exception:
            pass
    return {}


# ── Interview Results ────────────────────────────────────────────

async def save_interview_result(session_id: str, data: dict):
    _mem_interview_results[session_id] = data
    if _use_db:
        try:
            from app.db import save_interview_result as db_save
            await db_save(session_id, data)
        except Exception as e:
            logger.warning(f"DB save_interview_result failed: {e}")


async def get_interview_result(session_id: str) -> Optional[dict]:
    if session_id in _mem_interview_results:
        return _mem_interview_results[session_id]
    if _use_db:
        try:
            from app.db import get_interview_result as db_get
            return await db_get(session_id)
        except Exception:
            pass
    return None


async def list_interview_results() -> list[dict]:
    results = list(_mem_interview_results.values())
    if not results and _use_db:
        try:
            from app.db import list_interview_results_db
            return await list_interview_results_db()
        except Exception:
            pass
    return results


# ── Interview Questions ──────────────────────────────────────────

async def save_interview_questions(session_id: str, questions: list, meta: dict):
    _mem_interview_questions[session_id] = questions
    _mem_interview_question_meta[session_id] = meta
    if _use_db:
        try:
            from app.db import save_interview_questions as db_save
            await db_save(session_id, questions, meta)
        except Exception as e:
            logger.warning(f"DB save_interview_questions failed: {e}")


async def get_interview_questions(session_id: str) -> tuple[list, dict]:
    if session_id in _mem_interview_questions:
        return _mem_interview_questions[session_id], _mem_interview_question_meta.get(session_id, {})
    if _use_db:
        try:
            from app.db import get_interview_questions_db
            return await get_interview_questions_db(session_id)
        except Exception:
            pass
    return [], {}


async def list_pending_interviews() -> list[dict]:
    # In-memory: check which sessions have questions but no results
    pending = []
    for sid, meta in _mem_interview_question_meta.items():
        if sid not in _mem_interview_results:
            pending.append({
                "session_id": sid,
                "candidate_name": meta.get("name", "Unknown"),
                "candidate_email": meta.get("email", ""),
                "screening_score": meta.get("screening_score", 0),
                "status": "invite_sent",
                "questions_count": len(_mem_interview_questions.get(sid, [])),
            })
    if not pending and _use_db:
        try:
            from app.db import list_pending_interviews_db
            return await list_pending_interviews_db()
        except Exception:
            pass
    return pending


# ── Interview HR Decisions ───────────────────────────────────────

async def save_interview_hr_decision(session_id: str, decision: str, note: str = ""):
    _mem_interview_hr_decisions[session_id] = {"decision": decision, "note": note}
    # Also update the result record
    if session_id in _mem_interview_results:
        _mem_interview_results[session_id]["hr_decision"] = decision
    if _use_db:
        try:
            from app.db import update_interview_hr_decision
            await update_interview_hr_decision(session_id, decision)
        except Exception as e:
            logger.warning(f"DB save_interview_hr_decision failed: {e}")


async def get_interview_hr_decision(session_id: str) -> dict:
    return _mem_interview_hr_decisions.get(session_id, {})


# ── Scheduled Interviews ────────────────────────────────────────

async def save_scheduled_interview(interview_id: str, data: dict):
    _mem_scheduled[interview_id] = data
    if _use_db:
        try:
            from app.db import save_scheduled_interview as db_save
            await db_save({"id": interview_id, **data})
        except Exception as e:
            logger.warning(f"DB save_scheduled failed: {e}")


async def list_scheduled_interviews() -> list[dict]:
    results = list(_mem_scheduled.values())
    if not results and _use_db:
        try:
            from app.db import list_scheduled_interviews_db
            return await list_scheduled_interviews_db()
        except Exception:
            pass
    return results


# ── Applications ─────────────────────────────────────────────────

async def save_application(pipeline_id: str, app_data: dict):
    _mem_applications.setdefault(pipeline_id, []).append(app_data)
    if _use_db:
        try:
            from app.db import save_application as db_save
            await db_save(pipeline_id, app_data.get("name", ""), app_data.get("email", ""), app_data)
        except Exception as e:
            logger.warning(f"DB save_application failed: {e}")


async def get_applications(pipeline_id: str) -> list[dict]:
    if pipeline_id in _mem_applications:
        return _mem_applications[pipeline_id]
    if _use_db:
        try:
            from app.db import get_applications as db_get
            return await db_get(pipeline_id)
        except Exception:
            pass
    return []


# ── Shortlist Approvals ─────────────────────────────────────────

async def approve_shortlist(pipeline_id: str):
    _mem_shortlist_approved.add(pipeline_id)
    if _use_db:
        try:
            from app.db import approve_shortlist_db
            await approve_shortlist_db(pipeline_id)
        except Exception as e:
            logger.warning(f"DB approve_shortlist failed: {e}")


async def get_approved_shortlists() -> list[str]:
    results = list(_mem_shortlist_approved)
    if not results and _use_db:
        try:
            from app.db import get_approved_shortlists as db_get
            return await db_get()
        except Exception:
            pass
    return results
