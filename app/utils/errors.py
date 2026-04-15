"""Error handling — custom exceptions and error middleware.

Every node in the graph should catch errors gracefully:
- LLM failures → retry, then degrade to fallback
- External service failures → retry, then log and continue
- Never crash the pipeline for a non-critical failure
"""

import logging
import traceback
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Custom Exceptions ───────────────────────────────────────────────

class PipelineError(Exception):
    """Base error for pipeline failures."""
    def __init__(self, message: str, agent: str = "unknown", recoverable: bool = True):
        self.message = message
        self.agent = agent
        self.recoverable = recoverable
        super().__init__(message)


class LLMError(PipelineError):
    """LLM call failed after all retries."""
    def __init__(self, message: str):
        super().__init__(message, agent="llm", recoverable=True)


class ScreeningError(PipelineError):
    """Screening SOP failed for a candidate."""
    def __init__(self, message: str, candidate_name: str = "unknown"):
        self.candidate_name = candidate_name
        super().__init__(message, agent="agent_2", recoverable=True)


class InterviewError(PipelineError):
    """Avatar interview session failed."""
    def __init__(self, message: str, session_id: str = ""):
        self.session_id = session_id
        super().__init__(message, agent="agent_3", recoverable=True)


class ClawvatarError(PipelineError):
    """Clawvatar engine connection failed."""
    def __init__(self, message: str):
        super().__init__(message, agent="clawvatar", recoverable=True)


class ExternalServiceError(PipelineError):
    """Gmail, Calendar, or GitHub API failed."""
    def __init__(self, message: str, service: str = "unknown"):
        self.service = service
        super().__init__(message, agent=service, recoverable=True)


# ── Error Middleware ────────────────────────────────────────────────

async def error_handler_middleware(request: Request, call_next):
    """Global error handler — returns structured JSON errors, never crashes."""
    try:
        return await call_next(request)
    except PipelineError as e:
        logger.error(f"Pipeline error ({e.agent}): {e.message}")
        return JSONResponse(
            status_code=500 if not e.recoverable else 503,
            content={
                "error": "pipeline_error",
                "agent": e.agent,
                "message": e.message,
                "recoverable": e.recoverable,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.error(f"Unhandled error: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


# ── Safe Node Wrapper ───────────────────────────────────────────────

def safe_node(agent_name: str):
    """Decorator for LangGraph nodes — catches errors, logs them, and continues.

    Instead of crashing the pipeline, adds an error entry to the audit log
    and returns a degraded state.
    """
    def decorator(func):
        def wrapper(state):
            try:
                return func(state)
            except Exception as e:
                logger.error(
                    f"[{agent_name}] Node '{func.__name__}' failed: {e}\n"
                    f"{traceback.format_exc()}"
                )
                return {
                    "status": f"error_{func.__name__}",
                    "audit_log": [{
                        "agent": agent_name,
                        "action": f"error_{func.__name__}",
                        "explanation": f"Node failed: {str(e)}. Pipeline continues with degraded state.",
                        "data": {"error": str(e), "traceback": traceback.format_exc()[-500:]},
                    }],
                }
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator
