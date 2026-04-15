"""Fresher Resume Screening — 10-Step SOP.

Implements the exact HR screening procedure:
Step 1: Basic eligibility (pass/fail)
Step 2: Resume structure
Step 3: Educational qualification
Step 4: Skills match (critical)
Step 5: Academic projects
Step 6: Internship experience
Step 7: Certifications
Step 8: Initiative signals
Step 9: Score all dimensions
Step 10: Final verdict

Rule: Never auto-reject 45-59. Always flag for human review.
"""

import json
import logging
from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)


def screen_fresher(candidate: dict, jd_requirements: dict) -> dict:
    """Apply the full 10-step fresher screening SOP.

    Args:
        candidate: Parsed resume profile from resume_parser.
        jd_requirements: Skills matrix / JD requirements.

    Returns:
        Full screening result with dimension scores and verdict.
    """
    logger.info(f"Fresher screening: {candidate.get('name', 'unknown')}")

    prompt = f"""You are an expert HR professional screening a FRESHER candidate's resume.
Follow this EXACT 10-step procedure in order. Score only what is clearly written — never assume.

=== JD REQUIREMENTS ===
Role: {jd_requirements.get('role_title', 'unknown')}
Must-have skills: {[s.get('skill', s) if isinstance(s, dict) else s for s in jd_requirements.get('must_have', [])]}
Nice-to-have skills: {[s.get('skill', s) if isinstance(s, dict) else s for s in jd_requirements.get('nice_to_have', [])]}
Minimum education: {jd_requirements.get('min_education', 'Not specified')}
Graduation year range: {jd_requirements.get('graduation_year_range', 'Not specified')}
Min CGPA: {jd_requirements.get('min_cgpa', 'Not specified')}
Internship required: {jd_requirements.get('internship_required', False)}

=== CANDIDATE RESUME (JSON) ===
{json.dumps({
    "name": candidate.get("name", "unknown"),
    "total_experience_years": candidate.get("total_experience_years", 0),
    "education": candidate.get("education", []),
    "work_history": candidate.get("work_history", []),
    "internships": candidate.get("internships", []),
    "skills_evidenced": candidate.get("skills", {}).get("evidenced", []) if isinstance(candidate.get("skills"), dict) else [],
    "skills_claimed_only": candidate.get("skills", {}).get("claimed_only", []) if isinstance(candidate.get("skills"), dict) else [],
    "projects": candidate.get("projects", []),
    "certifications": candidate.get("certifications", []),
    "repo_urls": candidate.get("repo_urls", []),
    "initiative_signals": candidate.get("initiative_signals", []),
    "online_presence": candidate.get("online_presence", {}),
    "resume_quality": candidate.get("resume_quality", {}),
}, indent=2, default=str)}

=== PROCEDURE ===

STEP 1 — BASIC ELIGIBILITY (pass/fail gate):
- Does degree/stream match JD minimum? 
- Is graduation year in range?
- Does CGPA meet minimum (if specified)?
- If final year with results awaited → Conditional Pass
- If fail → Not Eligible, stop screening

STEP 2 — RESUME STRUCTURE:
- Clear sections? Contact info? Length (1-2 pages)? Spelling/grammar?
- No contact or blank template → flag for human review

STEP 3 — EDUCATIONAL QUALIFICATION:
- Degree, specialization, institution, year, GPA
- Academic honors, additional qualifications
- Unknown institution → flag (don't auto-reject)

STEP 4 — SKILLS MATCH (critical):
- Evidenced skills (in project/internship) → FULL credit
- Claimed skills (listed only) → HALF credit
- Calculate match %: (JD skills found / total required) × 100

STEP 5 — ACADEMIC PROJECTS:
- Relevance + JD tools. Self-initiated > college-assigned
- GitHub/demo links → strong positive
- One strong relevant > five vague unrelated

STEP 6 — INTERNSHIP:
- 2+ months relevant → strong. Semi-relevant → moderate
- Don't penalize heavily for no internship (unless JD requires it)

STEP 7 — CERTIFICATIONS:
- Higher weight: Google, AWS, Microsoft, Coursera, edX, Meta, NASSCOM
- Multiple relevant = self-learning signal

STEP 8 — INITIATIVE SIGNALS:
- Hackathons, leadership, GitHub, tech blogs, freelance, awards

STEP 9 — SCORE ALL DIMENSIONS:
Use this exact rubric:

| # | Dimension                 | Max | Scoring |
|---|---------------------------|-----|---------|
| 1 | Educational Qualification | 15  | Full match=13-15, Partial=7-12, No match=0 |
| 2 | Skills Match with JD      | 25  | Evidenced=full, Claimed=half, proportional |
| 3 | Internship Experience     | 20  | 2+mo relevant=16-20, Short=9-14, Not mandatory&absent=8, Mandatory&absent=0 |
| 4 | Academic Projects         | 15  | Relevant+JD tools=12-15, Partial=7-11, None=0 |
| 5 | Certifications            | 10  | 3+ relevant=8-10, 1-2=4-7, None=0 |
| 6 | Initiative Signals        | 10  | Multiple=8-10, One/two=4-6, None=0-2 |
| 7 | Resume Quality            | 5   | Clean=4-5, Minor issues=2-3, Poor=0-1 |

STEP 10 — FINAL VERDICT:
- 80-100: Priority Shortlist
- 60-79: Shortlisted
- 45-59: Flagged for Review (NEVER auto-reject)
- Below 45: Not Shortlisted

Return JSON:
{{
    "procedure": "fresher_10step",
    "eligibility": {{
        "passed": true,
        "status": "eligible | conditional_pass | not_eligible",
        "reason": "..."
    }},
    "dimension_scores": {{
        "educational_qualification": {{"score": 13, "max": 15, "notes": "..."}},
        "skills_match": {{"score": 18, "max": 25, "notes": "...", "match_pct": 72.5, "evidenced_count": 5, "claimed_count": 3}},
        "internship_experience": {{"score": 16, "max": 20, "notes": "..."}},
        "academic_projects": {{"score": 12, "max": 15, "notes": "..."}},
        "certifications": {{"score": 6, "max": 10, "notes": "..."}},
        "initiative_signals": {{"score": 8, "max": 10, "notes": "..."}},
        "resume_quality": {{"score": 4, "max": 5, "notes": "..."}}
    }},
    "total_score": 77,
    "max_score": 100,
    "verdict": "Priority Shortlist | Shortlisted | Flagged for Review | Not Shortlisted",
    "red_flags": [],
    "decision_note": "2-3 line summary: top strengths if shortlisted, specific gaps if not",
    "escalate_to_human": false,
    "top_strengths": ["...", "..."],
    "key_gaps": ["...", "..."]
}}

CRITICAL RULES:
- Score ONLY what is clearly visible in the resume
- Never assume or inflate scores
- 45-59 range → ALWAYS set escalate_to_human=true
- If any red flag → set escalate_to_human=true regardless of score
- A missed strong candidate is worse than an extra flag
"""

    try:
        result = call_llm_json(prompt)
        result["procedure"] = "fresher_10step"
        result["candidate_name"] = candidate.get("name", "unknown")

        logger.info(
            f"Fresher screening done: {candidate.get('name')} → "
            f"{result.get('total_score', 0)}/100 → {result.get('verdict', 'unknown')}"
            f"{' ⚠️ ESCALATED' if result.get('escalate_to_human') else ''}"
        )
        return result

    except Exception as e:
        logger.error(f"Fresher screening failed: {e}")
        return {
            "procedure": "fresher_10step",
            "candidate_name": candidate.get("name", "unknown"),
            "total_score": 0,
            "verdict": "Flagged for Review",
            "escalate_to_human": True,
            "red_flags": [f"Screening error: {str(e)}"],
            "decision_note": f"Automated screening failed — escalated for human review. Error: {e}",
            "error": str(e),
        }
