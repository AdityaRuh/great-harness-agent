"""Agent 1 — Skills Mapper, JD Drafter, Serializer, Publisher.
All sub-agents 2-5 in one module for Phase 1 simplicity."""

import json
import logging
from datetime import datetime, timezone

from app.integrations.llm import call_llm_json, call_llm_strong

logger = logging.getLogger(__name__)


# ── Sub-Agent 2: Skills Mapper ──────────────────────────────────────

def map_skills(tech_stack_profile: dict, role_title: str, experience_level: str) -> dict:
    """Classify tech stack into must-have vs. nice-to-have skills."""

    prompt = f"""Given this tech stack profile from a company's codebase,
classify skills for hiring a "{role_title}" with {experience_level} experience.

Tech Stack Profile:
{json.dumps(tech_stack_profile, indent=2)}

Return JSON:
{{
    "role_title": "{role_title}",
    "experience_level": "{experience_level}",
    "must_have": [
        {{"skill": "Next.js", "category": "framework", "reason": "Used in 6 frontend repos daily"}}
    ],
    "nice_to_have": [
        {{"skill": "LiveKit", "category": "library", "reason": "Only used in ruh-app-fe for video"}}
    ],
    "must_have_count": 8,
    "nice_to_have_count": 6
}}

Rules:
- Must-have: skills used across multiple repos or in the core codebase daily
- Nice-to-have: skills used in specific projects or peripherally
- For {experience_level} level, set appropriate depth expectations
- Include both technical skills and soft requirements"""

    result = call_llm_json(prompt)
    logger.info(f"Skills mapped: {result.get('must_have_count', 0)} must-have, "
                f"{result.get('nice_to_have_count', 0)} nice-to-have")
    return result


# ── Sub-Agent 3: JD Drafter ─────────────────────────────────────────

def draft_jd(
    skills_matrix: dict,
    tech_stack_profile: dict,
    role_title: str,
    experience_level: str,
    department: str,
    feedback: str | None = None,
) -> str:
    """Generate a complete job description using Claude Opus."""

    feedback_section = ""
    if feedback:
        feedback_section = f"""
IMPORTANT: The previous draft was reviewed and needs changes:
{feedback}

Please incorporate this feedback into the new draft."""

    prompt = f"""Write a professional, compelling job description for a {role_title} position
with {experience_level} experience in the {department} department.

This is for Ruh AI — an open-source platform for building AI employees.
The company builds conversational AI agents with Docker-native sandboxes.

Tech Stack (from actual codebase analysis):
{json.dumps(tech_stack_profile, indent=2)}

Skills Matrix:
{json.dumps(skills_matrix, indent=2)}
{feedback_section}

Write exactly 7 sections:

1. **About the Role** — 2-3 sentences, exciting, specific to the company
2. **What You'll Do** — 5-7 bullet points of actual responsibilities (based on the tech stack)
3. **Qualifications** — Must-have requirements (from skills_matrix.must_have)
4. **Nice to Have** — Bonus skills (from skills_matrix.nice_to_have)
5. **Benefits** — Standard startup benefits (remote-friendly, equity, learning budget, etc.)
6. **Location & Experience** — Remote/hybrid + {experience_level}
7. **About Ruh AI** — 2-3 sentences about the company mission

Write in a modern, approachable tone — not corporate jargon.
Use the actual technologies from the codebase, not generic buzzwords.
Output as clean Markdown."""

    jd = call_llm_strong(prompt)
    logger.info(f"JD drafted: {len(jd)} chars, {len(jd.split())} words")
    return jd


# ── Sub-Agent 4: JD Serializer ──────────────────────────────────────

def serialize_jd(jd_markdown: str, skills_matrix: dict, role_title: str) -> dict:
    """Convert JD markdown to structured JSON."""

    prompt = f"""Convert this job description to a structured JSON object.

Job Description:
{jd_markdown}

Skills Matrix:
{json.dumps(skills_matrix, indent=2)}

Return JSON:
{{
    "title": "{role_title}",
    "sections": {{
        "about_role": "...",
        "responsibilities": ["...", "..."],
        "qualifications": ["...", "..."],
        "nice_to_have": ["...", "..."],
        "benefits": ["...", "..."],
        "location_experience": "...",
        "about_company": "..."
    }},
    "metadata": {{
        "role_title": "{role_title}",
        "department": "Engineering",
        "skills_must_have": ["..."],
        "skills_nice_to_have": ["..."],
        "generated_at": "{datetime.now(timezone.utc).isoformat()}"
    }}
}}"""

    return call_llm_json(prompt)


# ── Sub-Agent 5: JD Publisher ───────────────────────────────────────

def publish_jd(jd_json: dict, jd_markdown: str, pipeline_id: str = "") -> str:
    """Publish JD to the built-in careers page.

    The JD becomes accessible at: GET /careers/{pipeline_id}
    This is a real, shareable URL.
    """
    from app.config import get_settings
    settings = get_settings()

    url = f"{settings.app_url}/careers/{pipeline_id}" if pipeline_id else f"{settings.app_url}/careers"
    logger.info(f"JD published: {jd_json.get('title', 'unknown')} → {url}")
    return url
