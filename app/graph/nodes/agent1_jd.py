"""LangGraph nodes for Agent 1 — JD Manager."""

import logging
from datetime import datetime, timezone

from app.agents.jd_manager.repo_analyzer import analyze_tech_stack
from app.agents.jd_manager.jd_pipeline import map_skills, draft_jd, serialize_jd, publish_jd
from app.graph.state import PipelineState

logger = logging.getLogger(__name__)


def agent1_analyze_repos(state: PipelineState) -> dict:
    """Node 1: Read knowledgebase → extract tech stack."""
    logger.info(f"[Agent 1 / SA-1] Analyzing repos for: {state['role_title']}")

    profile = analyze_tech_stack(state["role_title"], state["experience_level"])

    return {
        "tech_stack_profile": profile,
        "status": "analyzing",
        "audit_log": [{
            "agent": "agent_1",
            "action": "repo_analysis_complete",
            "explanation": f"Analyzed {len(profile.get('repos_analyzed', []))} repos. "
                          f"Primary language: {profile.get('primary_language', 'unknown')}. "
                          f"Found {len(profile.get('frameworks', []))} frameworks.",
            "data": {"repos": profile.get("repos_analyzed", [])},
        }],
    }


def agent1_map_skills(state: PipelineState) -> dict:
    """Node 2: Tech stack → must-have vs nice-to-have skills."""
    logger.info("[Agent 1 / SA-2] Mapping skills")

    matrix = map_skills(
        state["tech_stack_profile"],
        state["role_title"],
        state["experience_level"],
    )

    return {
        "skills_matrix": matrix,
        "audit_log": [{
            "agent": "agent_1",
            "action": "skills_mapped",
            "explanation": f"Classified {matrix.get('must_have_count', 0)} must-have and "
                          f"{matrix.get('nice_to_have_count', 0)} nice-to-have skills.",
            "data": matrix,
        }],
    }


def agent1_draft_jd(state: PipelineState) -> dict:
    """Node 3: Generate job description using Claude Opus."""
    logger.info("[Agent 1 / SA-3] Drafting JD")

    jd = draft_jd(
        skills_matrix=state["skills_matrix"],
        tech_stack_profile=state["tech_stack_profile"],
        role_title=state["role_title"],
        experience_level=state["experience_level"],
        department=state["department"],
        feedback=state.get("jd_feedback"),
    )

    return {
        "jd_draft": jd,
        "status": "jd_pending_approval",
        "current_checkpoint": "jd_approval",
        "audit_log": [{
            "agent": "agent_1",
            "action": "jd_drafted",
            "explanation": f"Generated {len(jd.split())} word JD for {state['role_title']}. "
                          f"Awaiting HR approval.",
            "data": {"word_count": len(jd.split())},
        }],
    }


def agent1_serialize_and_publish(state: PipelineState) -> dict:
    """Node 4+5: Serialize to JSON + publish (after approval)."""
    logger.info("[Agent 1 / SA-4+5] Serializing and publishing JD")

    jd_json = serialize_jd(state["jd_draft"], state["skills_matrix"], state["role_title"])
    url = publish_jd(jd_json, state["jd_draft"], pipeline_id=state["pipeline_id"])

    return {
        "jd_json": jd_json,
        "jd_published_url": url,
        "status": "jd_published",
        "current_checkpoint": None,
        "audit_log": [{
            "agent": "agent_1",
            "action": "jd_published",
            "explanation": f"JD serialized and published at {url}. Ready for resume screening.",
            "data": {"url": url},
        }],
    }
