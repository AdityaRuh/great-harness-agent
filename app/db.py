"""PostgreSQL database layer.

Replaces all in-memory dicts with persistent PostgreSQL storage.
Uses SQLAlchemy async for custom tables + psycopg for LangGraph PostgresSaver.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Text, Boolean, DateTime, JSON, create_engine
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class HRDecision(Base):
    __tablename__ = "hr_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(String, index=True)
    candidate_key = Column(String)  # email or name
    decision = Column(String)  # approve/reject
    note = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(String, index=True)
    name = Column(String)
    email = Column(String)
    data = Column(JSON, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class InterviewShortlistApproval(Base):
    __tablename__ = "interview_shortlist_approvals"
    pipeline_id = Column(String, primary_key=True)
    approved_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Engine & Session ─────────────────────────────────────────────

_async_engine = None
_async_session_factory = None
_sync_engine = None


def get_database_url():
    """Get database URL from settings or env."""
    import os
    return os.environ.get("DATABASE_URL", settings.database_url)


def get_async_database_url():
    """Convert sync URL to async."""
    url = get_database_url()
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def init_db():
    """Initialize database — create tables if they don't exist."""
    global _async_engine, _async_session_factory, _sync_engine

    async_url = get_async_database_url()
    sync_url = get_database_url()

    logger.info(f"Connecting to database: {sync_url.split('@')[1] if '@' in sync_url else sync_url}")

    _async_engine = create_async_engine(async_url, pool_size=10, max_overflow=20)
    _async_session_factory = async_sessionmaker(_async_engine, class_=AsyncSession, expire_on_commit=False)

    _sync_engine = create_engine(sync_url, pool_size=5, max_overflow=10)

    # Create tables
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables created/verified")


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
