"""Shortlist aggregator — composite ranking from screening + interview scores."""

import logging

logger = logging.getLogger(__name__)

SCREENING_WEIGHT = 0.4
INTERVIEW_WEIGHT = 0.6


def aggregate_shortlist(
    ranked_candidates: list[dict],
    interview_results: list[dict],
) -> list[dict]:
    """Combine screening and interview scores into final shortlist.

    Formula: final_score = 0.4 * screening_rank_score + 0.6 * interview_score
    """
    # Build lookup: candidate_name → interview_result
    interview_map = {}
    for result in interview_results:
        name = result.get("candidate_name", "")
        interview_map[name] = result

    shortlist = []
    for candidate in ranked_candidates:
        profile = candidate.get("candidate", {})
        name = profile.get("name", "unknown")
        screening_score = candidate.get("screening_result", {}).get("total_score", 0)

        interview = interview_map.get(name, {})
        interview_score = interview.get("total_score", 0)

        final_score = round(
            SCREENING_WEIGHT * screening_score + INTERVIEW_WEIGHT * interview_score, 1
        )

        shortlist.append({
            "name": name,
            "email": profile.get("email"),
            "screening_score": screening_score,
            "interview_score": interview_score,
            "final_score": final_score,
            "tier": candidate.get("tier"),
            "screening_verdict": candidate.get("screening_result", {}).get("verdict"),
            "interview_status": interview.get("status", "not_interviewed"),
            "questions_answered": interview.get("questions_answered", 0),
            "interview_duration": interview.get("duration_minutes"),
        })

    # Sort by final score descending
    shortlist.sort(key=lambda x: x["final_score"], reverse=True)

    # Assign final rank
    for i, entry in enumerate(shortlist):
        entry["final_rank"] = i + 1

    logger.info(
        f"Final shortlist: {len(shortlist)} candidates, "
        f"top score: {shortlist[0]['final_score'] if shortlist else 0}"
    )
    return shortlist
