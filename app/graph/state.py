"""Pipeline state — shared across all LangGraph nodes.

CRITICAL: All list fields use Annotated reducers so that candidates
can be added from careers form, Agent 2, and Agent 3 without
"concurrent update" errors. This is the single source of truth.
"""

from typing import Annotated, Optional
from typing_extensions import TypedDict


def _replace_list(old: list, new: list) -> list:
    """Replace the entire list (last write wins)."""
    return new


def _append_list(old: list, new: list) -> list:
    """Append new items to existing list."""
    return old + new


class PipelineState(TypedDict):
    # Config (set at pipeline creation)
    pipeline_id: str
    role_title: str
    experience_level: str
    candidate_type: str  # "fresher" | "experienced" | "auto"
    department: str

    # Agent 1 — JD Manager
    tech_stack_profile: Optional[dict]
    skills_matrix: Optional[dict]
    jd_draft: Optional[str]
    jd_feedback: Optional[str]
    jd_approved: Optional[bool]
    jd_json: Optional[dict]
    jd_published_url: Optional[str]

    # Agent 2 — Screener (reducers allow updates from careers form + LangGraph nodes)
    candidates: Annotated[list[dict], _replace_list]
    ranked_candidates: Annotated[list[dict], _replace_list]
    ranking_approved: Optional[bool]

    # Agent 3 — Avatar Interviewer
    interview_questions: Annotated[list[dict], _replace_list]
    interview_results: Annotated[list[dict], _replace_list]
    final_shortlist: Annotated[list[dict], _replace_list]
    shortlist_approved: Optional[bool]

    # Agent 4 — Scheduler
    scheduled_interviews: Annotated[list[dict], _replace_list]
    verdicts: Annotated[list[dict], _replace_list]
    offers_sent: Annotated[list[str], _replace_list]
    rejections_sent: Annotated[list[str], _replace_list]

    # Meta
    status: str
    current_checkpoint: Optional[str]
    audit_log: Annotated[list[dict], _append_list]  # append-only
