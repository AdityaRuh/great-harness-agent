"""Two-tier candidate ranker.

Tier A: Candidates with GitHub repos → screening_score + repo_score
Tier B: Candidates without repos → screening_score + skill_match_score
All Tier A ranked above all Tier B.
"""

import logging
from app.agents.screener.repo_analyzer import analyze_candidate_repo

logger = logging.getLogger(__name__)


def rank_candidates(
    screened_candidates: list[dict],
    tech_stack_profile: dict,
) -> list[dict]:
    """Rank all screened candidates into Tier A (repo-backed) and Tier B (no repo).

    Args:
        screened_candidates: List of {candidate, screening_result} dicts.
        tech_stack_profile: From Agent 1 for tech overlap scoring.

    Returns:
        Ranked list with tier, scores, and final rank.
    """
    tier_a = []  # Candidates with repos
    tier_b = []  # Candidates without repos

    for entry in screened_candidates:
        candidate = entry.get("candidate", entry.get("parsed_profile", entry))
        screening = entry.get("screening_result", {})

        # Skip ineligible candidates
        verdict = screening.get("verdict", "")
        if verdict == "Not Shortlisted" and not screening.get("escalate_to_human"):
            entry["tier"] = None
            entry["final_rank"] = None
            entry["status"] = "rejected"
            continue

        screening_score = screening.get("total_score", 0)
        repo_urls = candidate.get("repo_urls", [])

        if repo_urls:
            # Tier A — analyze repos
            repo_score = _analyze_repos(repo_urls, tech_stack_profile)
            composite = round(0.5 * screening_score + 0.5 * repo_score, 1)

            entry["tier"] = "A"
            entry["repo_score"] = repo_score
            entry["composite_score"] = composite
            tier_a.append(entry)
        else:
            # Tier B — skill match only (screening score IS the skill match)
            composite = round(0.7 * screening_score + 0.3 * screening_score, 1)  # Pure screening
            entry["tier"] = "B"
            entry["repo_score"] = None
            entry["composite_score"] = composite
            tier_b.append(entry)

    # Sort within tiers
    tier_a.sort(key=lambda x: x["composite_score"], reverse=True)
    tier_b.sort(key=lambda x: x["composite_score"], reverse=True)

    # Tier A above Tier B
    ranked = tier_a + tier_b

    # Assign final ranks
    for i, entry in enumerate(ranked):
        entry["final_rank"] = i + 1

    logger.info(
        f"Ranking complete: {len(tier_a)} Tier A, {len(tier_b)} Tier B, "
        f"{len(screened_candidates) - len(ranked)} rejected"
    )

    return ranked


def _analyze_repos(repo_urls: list[str], tech_stack_profile: dict) -> float:
    """Analyze candidate's repos and return average repo score."""
    scores = []
    for url in repo_urls[:3]:  # Max 3 repos to avoid timeout
        try:
            result = analyze_candidate_repo(url, tech_stack_profile)
            score = result.get("repo_score", 0)
            scores.append(score)
        except Exception as e:
            logger.warning(f"Repo analysis failed for {url}: {e}")
            continue

    if not scores:
        return 0.0

    return round(sum(scores) / len(scores), 1)


def format_ranking_summary(ranked: list[dict]) -> str:
    """Format a human-readable ranking summary for checkpoint review."""
    lines = ["# Candidate Ranking\n"]

    tier_a = [r for r in ranked if r.get("tier") == "A"]
    tier_b = [r for r in ranked if r.get("tier") == "B"]
    flagged = [r for r in ranked if r.get("screening_result", {}).get("escalate_to_human")]

    if tier_a:
        lines.append(f"## Tier A — Repo-Backed ({len(tier_a)} candidates)\n")
        for r in tier_a:
            name = r.get("candidate", {}).get("name", "unknown")
            score = r.get("composite_score", 0)
            repo = r.get("repo_score", 0)
            screening = r.get("screening_result", {}).get("total_score", 0)
            verdict = r.get("screening_result", {}).get("verdict", "")
            lines.append(f"  Rank {r['final_rank']}: {name} — {score}/100 "
                        f"(screening: {screening}, repo: {repo}) [{verdict}]")

    if tier_b:
        lines.append(f"\n## Tier B — No Repo ({len(tier_b)} candidates)\n")
        for r in tier_b:
            name = r.get("candidate", {}).get("name", "unknown")
            score = r.get("composite_score", 0)
            screening = r.get("screening_result", {}).get("total_score", 0)
            verdict = r.get("screening_result", {}).get("verdict", "")
            lines.append(f"  Rank {r['final_rank']}: {name} — {score}/100 "
                        f"(screening: {screening}) [{verdict}]")

    if flagged:
        lines.append(f"\n## ⚠️ Flagged for Human Review ({len(flagged)})\n")
        for r in flagged:
            name = r.get("candidate", {}).get("name", "unknown")
            flags = r.get("screening_result", {}).get("red_flags", [])
            lines.append(f"  {name}: {', '.join(flags) if flags else 'Borderline score'}")

    return "\n".join(lines)
