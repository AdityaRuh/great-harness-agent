"""PostgreSQL database layer.

Replaces all in-memory dicts with persistent PostgreSQL storage.
Uses SQLAlchemy async for custom tables + psycopg for LangGraph PostgresSaver.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Text, Boolean, DateTime, JSON, create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import text

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── SQLAlchemy Setup ─────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Pipeline(Base):
    __tablename__ = "pipelines"
    id = Column(String, primary_key=True)
    config = Column(JSON, default={})
    last_status = Column(String, default="unknown")
    last_state_cache = Column(JSON, default={})
    created_at = Column(DateTime, default=lambda: datetime.utcnow())


class HRDecision(Base):
    __tablename__ = "hr_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(String, index=True)
    candidate_key = Column(String)  # email or name
    decision = Column(String)  # approve/reject
    note = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.utcnow())


class InterviewResult(Base):
    __tablename__ = "interview_results"
    session_id = Column(String, primary_key=True)
    candidate_name = Column(String)
    candidate_email = Column(String, default="")
    screening_score = Column(Float, default=0)
    interview_score = Column(Float, default=0)
    composite_score = Column(Float, default=0)
    shortlisted = Column(Boolean, default=False)
    shortlist_verdict = Column(String, default="")
    transcript = Column(JSON, default=[])
    evaluation = Column(JSON, default={})
    hr_decision = Column(String, default="")  # qualify/reject
    created_at = Column(DateTime, default=lambda: datetime.utcnow())


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"
    session_id = Column(String, primary_key=True)
    candidate_name = Column(String, default="")
    candidate_email = Column(String, default="")
    screening_score = Column(Float, default=0)
    questions = Column(JSON, default=[])


class ScheduledInterview(Base):
    __tablename__ = "scheduled_interviews"
    id = Column(String, primary_key=True)
    candidate_name = Column(String)
    candidate_email = Column(String, default="")
    pipeline_id = Column(String, default="")
    date = Column(String)
    time = Column(String)
    duration_minutes = Column(Integer, default=60)
    interviewers = Column(JSON, default=[])
    meet_link = Column(String, default="")
    calendar_event_id = Column(String, default="")
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, default=lambda: datetime.utcnow())


class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(String, index=True)
    name = Column(String)
    email = Column(String)
    data = Column(JSON, default={})
    created_at = Column(DateTime, default=lambda: datetime.utcnow())


class InterviewShortlistApproval(Base):
    __tablename__ = "interview_shortlist_approvals"
    pipeline_id = Column(String, primary_key=True)
    approved_at = Column(DateTime, default=lambda: datetime.utcnow())


# ── Engine & Session ─────────────────────────────────────────────

_async_engine = None
_async_session_factory = None
_sync_engine = None


def get_database_url():
    """Get database URL from settings or env."""
    import os
    url = os.environ.get("DATABASE_URL", settings.database_url)
    # Remove channel_binding param (not supported by all drivers)
    if "channel_binding" in url:
        import re
        url = re.sub(r'[&?]channel_binding=[^&]*', '', url)
    return url


def get_async_database_url():
    """Convert sync URL to async."""
    import re
    url = get_database_url()
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    # asyncpg doesn't support sslmode param — remove it (we'll set ssl in connect_args)
    url = re.sub(r'[?&]sslmode=[^&]*', '', url)
    # Clean up leftover ? or &
    url = url.replace('?&', '?').rstrip('?')
    return url


def get_sync_database_url():
    """Get sync URL with psycopg v3 driver (not psycopg2)."""
    url = get_database_url()
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


async def init_db():
    """Initialize database — create tables if they don't exist."""
    global _async_engine, _async_session_factory, _sync_engine

    async_url = get_async_database_url()
    sync_url = get_sync_database_url()

    logger.info(f"Connecting to database: {sync_url.split('@')[1] if '@' in sync_url else sync_url}")

    # Detect if remote DB (needs SSL)
    import ssl as _ssl
    needs_ssl = "neon.tech" in async_url or "supabase" in async_url or "amazonaws" in async_url
    if needs_ssl:
        ssl_ctx = _ssl.create_default_context()
        connect_args = {"ssl": ssl_ctx}
    else:
        connect_args = {}
    _async_engine = create_async_engine(async_url, pool_size=5, max_overflow=10, connect_args=connect_args, pool_timeout=10)
    _async_session_factory = async_sessionmaker(_async_engine, class_=AsyncSession, expire_on_commit=False)

    _sync_engine = create_engine(sync_url, pool_size=3, max_overflow=5, pool_timeout=10)

    # Create/update tables (drop first to fix schema mismatches during development)
    try:
        async with _async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables recreated successfully")
    except Exception as e:
        logger.warning(f"Table creation issue: {e}")
        # Try create without drop
        try:
            async with _async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created (fallback)")
        except Exception as e2:
            logger.warning(f"Table fallback also failed: {e2}")


async def close_db():
    """Close database connections."""
    global _async_engine, _sync_engine
    if _async_engine:
        await _async_engine.dispose()
    if _sync_engine:
        _sync_engine.dispose()


def get_session() -> AsyncSession:
    """Get an async database session."""
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _async_session_factory()


def get_sync_engine():
    """Get sync engine for LangGraph PostgresSaver."""
    return _sync_engine


# ── Helper Functions ─────────────────────────────────────────────

async def save_pipeline(pipeline_id: str, config: dict, status: str = "unknown"):
    async with get_session() as session:
        existing = await session.get(Pipeline, pipeline_id)
        if existing:
            existing.config = config
            existing.last_status = status
        else:
            session.add(Pipeline(id=pipeline_id, config=config, last_status=status))
        await session.commit()
        logger.info(f"Pipeline {pipeline_id[:8]} saved to DB (status={status})")


async def update_pipeline_cache(pipeline_id: str, status: str, state_cache: dict):
    async with get_session() as session:
        existing = await session.get(Pipeline, pipeline_id)
        if existing:
            existing.last_status = status
            existing.last_state_cache = state_cache
            await session.commit()


async def get_pipeline_data(pipeline_id: str) -> dict | None:
    async with get_session() as session:
        p = await session.get(Pipeline, pipeline_id)
        if p:
            return {"id": p.id, "config": p.config, "last_status": p.last_status,
                    "last_state_cache": p.last_state_cache or {}, "created_at": str(p.created_at)}
        return None


async def list_pipelines_db() -> list[dict]:
    async with get_session() as session:
        result = await session.execute(text("SELECT id, config, last_status, created_at FROM pipelines ORDER BY created_at DESC"))
        return [{"id": r[0], "config": r[1], "last_status": r[2], "created_at": str(r[3])} for r in result]


async def save_hr_decision(pipeline_id: str, candidate_key: str, decision: str, note: str = ""):
    async with get_session() as session:
        # Upsert
        result = await session.execute(
            text("SELECT id FROM hr_decisions WHERE pipeline_id = :pid AND candidate_key = :ck"),
            {"pid": pipeline_id, "ck": candidate_key}
        )
        existing = result.first()
        if existing:
            await session.execute(
                text("UPDATE hr_decisions SET decision = :d, note = :n WHERE id = :id"),
                {"d": decision, "n": note, "id": existing[0]}
            )
        else:
            session.add(HRDecision(pipeline_id=pipeline_id, candidate_key=candidate_key, decision=decision, note=note))
        await session.commit()


async def get_hr_decisions(pipeline_id: str) -> dict:
    async with get_session() as session:
        result = await session.execute(
            text("SELECT candidate_key, decision, note FROM hr_decisions WHERE pipeline_id = :pid"),
            {"pid": pipeline_id}
        )
        return {r[0]: {"decision": r[1], "note": r[2]} for r in result}


async def save_interview_result(session_id: str, data: dict):
    async with get_session() as session:
        existing = await session.get(InterviewResult, session_id)
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        else:
            session.add(InterviewResult(session_id=session_id, **{k: v for k, v in data.items() if hasattr(InterviewResult, k)}))
        await session.commit()


async def get_interview_result(session_id: str) -> dict | None:
    async with get_session() as session:
        r = await session.get(InterviewResult, session_id)
        if r:
            return {"session_id": r.session_id, "candidate_name": r.candidate_name,
                    "candidate_email": r.candidate_email, "screening_score": r.screening_score,
                    "interview_score": r.interview_score, "composite_score": r.composite_score,
                    "shortlisted": r.shortlisted, "shortlist_verdict": r.shortlist_verdict,
                    "transcript": r.transcript, "evaluation": r.evaluation,
                    "hr_decision": r.hr_decision}
        return None


async def list_interview_results_db() -> list[dict]:
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT session_id, candidate_name, candidate_email, screening_score, interview_score, "
            "composite_score, shortlisted, shortlist_verdict, hr_decision FROM interview_results ORDER BY created_at DESC"
        ))
        return [{"session_id": r[0], "candidate_name": r[1], "candidate_email": r[2],
                 "screening_score": r[3], "interview_score": r[4], "composite_score": r[5],
                 "shortlisted": r[6], "shortlist_verdict": r[7], "hr_decision": r[8]} for r in result]


async def save_interview_questions(session_id: str, questions: list, meta: dict):
    async with get_session() as session:
        existing = await session.get(InterviewQuestion, session_id)
        if existing:
            existing.questions = questions
            existing.candidate_name = meta.get("name", "")
            existing.candidate_email = meta.get("email", "")
            existing.screening_score = meta.get("screening_score", 0)
        else:
            session.add(InterviewQuestion(
                session_id=session_id, questions=questions,
                candidate_name=meta.get("name", ""),
                candidate_email=meta.get("email", ""),
                screening_score=meta.get("screening_score", 0),
            ))
        await session.commit()


async def get_interview_questions_db(session_id: str) -> tuple[list, dict]:
    async with get_session() as session:
        q = await session.get(InterviewQuestion, session_id)
        if q:
            return q.questions or [], {"name": q.candidate_name, "email": q.candidate_email, "screening_score": q.screening_score}
        return [], {}


async def list_pending_interviews_db() -> list[dict]:
    """Get interviews that are pending (questions stored but no result yet)."""
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT iq.session_id, iq.candidate_name, iq.candidate_email, iq.screening_score, "
            "json_array_length(iq.questions::json) as q_count "
            "FROM interview_questions iq "
            "LEFT JOIN interview_results ir ON iq.session_id = ir.session_id "
            "WHERE ir.session_id IS NULL"
        ))
        return [{"session_id": r[0], "candidate_name": r[1], "candidate_email": r[2],
                 "screening_score": r[3], "questions_count": r[4], "status": "invite_sent"} for r in result]


async def save_scheduled_interview(data: dict):
    async with get_session() as session:
        sid = data.get("id", f"{data.get('candidate_name', 'unknown')}_{data.get('date', '')}")
        existing = await session.get(ScheduledInterview, sid)
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        else:
            session.add(ScheduledInterview(id=sid, **{k: v for k, v in data.items() if k != "id" and hasattr(ScheduledInterview, k)}))
        await session.commit()


async def list_scheduled_interviews_db() -> list[dict]:
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT id, candidate_name, candidate_email, pipeline_id, date, time, "
            "duration_minutes, interviewers, meet_link, calendar_event_id, status "
            "FROM scheduled_interviews ORDER BY created_at DESC"
        ))
        return [{"id": r[0], "candidate_name": r[1], "candidate_email": r[2],
                 "pipeline_id": r[3], "date": r[4], "time": r[5],
                 "duration_minutes": r[6], "interviewers": r[7], "meet_link": r[8],
                 "calendar_event_id": r[9], "status": r[10]} for r in result]


async def save_application(pipeline_id: str, name: str, email: str, data: dict):
    async with get_session() as session:
        session.add(Application(pipeline_id=pipeline_id, name=name, email=email, data=data))
        await session.commit()


async def get_applications(pipeline_id: str) -> list[dict]:
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, email, data FROM applications WHERE pipeline_id = :pid ORDER BY created_at DESC"),
            {"pid": pipeline_id}
        )
        return [{"name": r[0], "email": r[1], **r[2]} for r in result]


async def approve_shortlist_db(pipeline_id: str):
    async with get_session() as session:
        existing = await session.get(InterviewShortlistApproval, pipeline_id)
        if not existing:
            session.add(InterviewShortlistApproval(pipeline_id=pipeline_id))
            await session.commit()


async def get_approved_shortlists() -> list[str]:
    async with get_session() as session:
        result = await session.execute(text("SELECT pipeline_id FROM interview_shortlist_approvals"))
        return [r[0] for r in result]


async def update_interview_hr_decision(session_id: str, decision: str):
    async with get_session() as session:
        existing = await session.get(InterviewResult, session_id)
        if existing:
            existing.hr_decision = decision
            await session.commit()
            return True
        return False


async def list_scheduled_db() -> list[dict]:
    """List all scheduled interviews from DB."""
    async with get_session() as session:
        result = await session.execute(select(ScheduledInterview))
        rows = result.scalars().all()
        return [{"session_id": r.id, "candidate_name": r.candidate_name, "candidate_email": r.candidate_email,
                 "date": r.date, "time": r.time, "meet_link": r.meet_link, "status": r.status,
                 "interviewers": r.interviewers or []} for r in rows]


async def save_scheduled_db(session_id: str, data: dict):
    """Save scheduled interview to DB."""
    async with get_session() as session:
        existing = await session.get(ScheduledInterview, session_id)
        if existing:
            existing.candidate_name = data.get("candidate_name", "")
            existing.candidate_email = data.get("candidate_email", "")
            existing.date = data.get("date", "")
            existing.time = data.get("time", "")
            existing.meet_link = data.get("meet_link", "")
            existing.status = data.get("status", "scheduled")
            existing.interviewers = data.get("interviewers", [])
        else:
            session.add(ScheduledInterview(
                id=session_id, candidate_name=data.get("candidate_name", ""),
                candidate_email=data.get("candidate_email", ""),
                date=data.get("date", ""), time=data.get("time", ""),
                meet_link=data.get("meet_link", ""), status=data.get("status", "scheduled"),
                interviewers=data.get("interviewers", []),
            ))
        await session.commit()


async def list_hr_decisions_db() -> list[dict]:
    """List all HR interview decisions from DB."""
    async with get_session() as session:
        result = await session.execute(select(InterviewResult).where(InterviewResult.hr_decision.isnot(None)))
        rows = result.scalars().all()
        return [{"session_id": r.id, "decision": r.hr_decision, "note": ""} for r in rows if r.hr_decision]


async def get_hr_decisions_for_pipeline(pipeline_id: str) -> dict:
    """Get HR decisions for a pipeline from DB."""
    async with get_session() as session:
        result = await session.execute(
            select(HRDecision).where(HRDecision.pipeline_id == pipeline_id)
        )
        rows = result.scalars().all()
        decisions = {}
        for r in rows:
            decisions[r.candidate_key] = {"decision": r.decision, "note": r.note or ""}
        return decisions


async def save_hr_decision_db(pipeline_id: str, candidate_key: str, decision: str, note: str = ""):
    """Save HR decision to DB."""
    async with get_session() as session:
        key = f"{pipeline_id}:{candidate_key}"
        existing = await session.get(HRDecision, key)
        if existing:
            existing.decision = decision
            existing.note = note
        else:
            session.add(HRDecision(
                id=key, pipeline_id=pipeline_id,
                candidate_key=candidate_key, decision=decision, note=note,
            ))
        await session.commit()
