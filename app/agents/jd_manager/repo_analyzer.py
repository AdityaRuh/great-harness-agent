"""Agent 1, Sub-Agent 1 — Repo Analyzer.
Reads ruh-org-knowledgebase to extract tech stack for the role."""

import logging

from app.integrations.knowledgebase import get_repos_by_role, read_index
from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)


def analyze_tech_stack(role_title: str, experience_level: str) -> dict:
    """Read knowledgebase repos relevant to the role → extract tech stack profile."""

    logger.info(f"Analyzing tech stack for: {role_title} ({experience_level})")

    # Get relevant repos from knowledgebase
    repos = get_repos_by_role(role_title)
    if not repos:
        logger.warning("No repos found in knowledgebase, using defaults")
        return _default_profile(role_title)

    # Read index for context
    index = read_index()

    # Build catalog summary for LLM
    catalog_summary = ""
    for name, content in repos.items():
        # Take first 60 lines of each catalog entry (tech stack section)
        lines = content.split("\n")[:60]
        catalog_summary += f"\n--- {name} ---\n" + "\n".join(lines) + "\n"

    prompt = f"""Analyze these repository profiles from a company's codebase.
Extract the tech stack relevant to hiring a "{role_title}" with {experience_level} experience.

Organization Index:
{index[:1000]}

Repository Profiles:
{catalog_summary}

Return a JSON object with:
{{
    "role_title": "{role_title}",
    "primary_language": "TypeScript | Python | etc",
    "frameworks": ["Next.js 15", "React 19", ...],
    "libraries": ["Radix UI", "React Query", ...],
    "databases": ["PostgreSQL", "MongoDB", ...],
    "infrastructure": ["Docker", "Kubernetes", ...],
    "testing": ["Playwright", "Jest", ...],
    "build_tools": ["Bun", "Turbopack", ...],
    "communication": ["gRPC", "REST", "WebSocket", "Kafka", ...],
    "repos_analyzed": ["repo-name-1", "repo-name-2", ...],
    "summary": "2-3 sentence summary of the tech environment"
}}"""

    try:
        profile = call_llm_json(prompt)
        profile["repos_analyzed"] = list(repos.keys())
        logger.info(f"Tech stack extracted: {len(profile.get('frameworks', []))} frameworks, "
                     f"{len(repos)} repos analyzed")
        return profile
    except Exception as e:
        logger.error(f"LLM extraction failed: {e}, using defaults")
        return _default_profile(role_title)


def _default_profile(role_title: str) -> dict:
    """Fallback if knowledgebase is unavailable."""
    return {
        "role_title": role_title,
        "primary_language": "unknown",
        "frameworks": [],
        "libraries": [],
        "databases": [],
        "infrastructure": [],
        "testing": [],
        "build_tools": [],
        "communication": [],
        "repos_analyzed": [],
        "summary": f"Default profile for {role_title} — knowledgebase not available",
    }
