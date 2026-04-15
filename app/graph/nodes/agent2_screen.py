"""LangGraph nodes for Agent 2 — Resume Screener & Ranker.

parse_resumes → screen_candidates (fresher 10-step OR experienced 8-step) → rank_candidates
"""

import logging

from app.agents.screener.resume_parser import parse_resume
from app.agents.screener.fresher_screener import screen_fresher
from app.agents.screener.experienced_screener import screen_experienced
from app.agents.screener.ranker import rank_candidates, format_ranking_summary
from app.graph.state import PipelineState

logger = logging.getLogger(__name__)


def agent2_parse_resumes(state: PipelineState) -> dict:
    """Parse all uploaded resumes into structured profiles."""
    raw_candidates = state.get("candidates", [])
    if not raw_candidates:
        return {"status": "screening_no_candidates", "audit_log": [{"agent": "agent_2", "action": "no_resumes", "explanation": "No resumes uploaded."}]}

    logger.info(f"[Agent 2 / SA-1] Parsing {len(raw_candidates)} resumes")

    skills_matrix = state.get("skills_matrix", {})
    jd_skills = [s.get("skill", s) if isinstance(s, dict) else s for s in skills_matrix.get("must_have", [])]

    parsed = []
    for i, raw in enumerate(raw_candidates):
        if raw.get("parsed_profile"):
            parsed.append(raw)
            continue

        file_bytes = raw.get("file_bytes", b"")
        filename = raw.get("filename", "unknown.pdf")
        profile = parse_resume(file_bytes, filename, jd_skills)
        raw["parsed_profile"] = profile

        years = profile.get("total_experience_years", 0)
        raw["candidate_type"] = "experienced" if years >= 3 else "fresher" if state.get("candidate_type") == "auto" else state.get("candidate_type", "fresher")
        parsed.append(raw)

    freshers = sum(1 for c in parsed if c.get("candidate_type") == "fresher")
    experienced = sum(1 for c in parsed if c.get("candidate_type") == "experienced")

    return {
        "candidates": parsed,
        "status": "resumes_parsed",
        "audit_log": [{"agent": "agent_2", "action": "resumes_parsed",
            "explanation": f"Parsed {len(parsed)} resumes. Freshers: {freshers}, Experienced: {experienced}.",
            "data": {"total": len(parsed), "freshers": freshers, "experienced": experienced}}],
    }


def agent2_screen_candidates(state: PipelineState) -> dict:
    """Screen each candidate using fresher 10-step or experienced 8-step SOP."""
    candidates = state.get("candidates", [])
    skills_matrix = state.get("skills_matrix", {})
    tech_stack = state.get("tech_stack_profile", {})

    jd_requirements = {
        "role_title": state.get("role_title", "unknown"),
        "experience_level": state.get("experience_level", ""),
        "must_have": skills_matrix.get("must_have", []),
        "nice_to_have": skills_matrix.get("nice_to_have", []),
        "frameworks": tech_stack.get("frameworks", []),
        "min_education": skills_matrix.get("min_education"),
        "internship_required": skills_matrix.get("internship_required", False),
    }

    shortlisted, flagged, rejected = 0, 0, 0

    for i, entry in enumerate(candidates):
        profile = entry.get("parsed_profile", {})
        ctype = entry.get("candidate_type", "fresher")
        name = profile.get("name", "unknown")
        logger.info(f"  Screening {i+1}/{len(candidates)}: {name} ({ctype})")

        if ctype == "experienced":
            result = screen_experienced(profile, jd_requirements)
        else:
            result = screen_fresher(profile, jd_requirements)

        entry["screening_result"] = result
        verdict = result.get("verdict", "")
        if "Shortlisted" in verdict or "Priority" in verdict: shortlisted += 1
        elif "Flagged" in verdict: flagged += 1
        else: rejected += 1

    return {
        "candidates": candidates,
        "status": "screening_complete",
        "audit_log": [{"agent": "agent_2", "action": "screening_complete",
            "explanation": f"Screened {len(candidates)} candidates. Shortlisted: {shortlisted}, Flagged: {flagged}, Rejected: {rejected}.",
            "data": {"total": len(candidates), "shortlisted": shortlisted, "flagged": flagged, "rejected": rejected}}],
    }


def agent2_rank_candidates(state: PipelineState) -> dict:
    """Rank into Tier A (repo-backed) above Tier B (no repo)."""
    candidates = state.get("candidates", [])
    tech_stack = state.get("tech_stack_profile", {})

    screened_input = []
    for entry in candidates:
        screening = entry.get("screening_result", {})
        verdict = screening.get("verdict", "")
        if "Not Shortlisted" in verdict and not screening.get("escalate_to_human"):
            continue
        screened_input.append({"candidate": entry.get("parsed_profile", {}), "screening_result": screening})

    ranked = rank_candidates(screened_input, tech_stack)
    summary = format_ranking_summary(ranked)
    tier_a = sum(1 for r in ranked if r.get("tier") == "A")
    tier_b = sum(1 for r in ranked if r.get("tier") == "B")

    return {
        "ranked_candidates": ranked,
        "status": "ranking_complete",
        "current_checkpoint": "ranking_review",
        "audit_log": [{"agent": "agent_2", "action": "ranking_complete",
            "explanation": f"Ranked {len(ranked)} candidates. Tier A: {tier_a}, Tier B: {tier_b}. Awaiting HR review.\n\n{summary}",
            "data": {"total": len(ranked), "tier_a": tier_a, "tier_b": tier_b}}],
    }
