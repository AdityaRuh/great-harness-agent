"""Experienced Candidate Screening — 8-Step SOP.

For candidates with ≥ 3 years hands-on technical experience.
Focus: depth of work, quality of contributions, career trajectory, real-world impact.

Step 1: Resume structure
Step 2: Verify relevant experience
Step 3: Deep-dive work experience (MOST CRITICAL)
  3a: Job titles and progression
  3b: Role descriptions for depth
  3c: Ownership and leadership
  3d: Quantified impact
  3e: Technical stack
Step 4: Technical skills (with caution)
Step 5: Projects and open source
Step 6: Education and certifications (secondary)
Step 7: Career stability
Step 8: Final verdict

Auto red flags → always escalate regardless of score.
Rule: When in doubt, flag for human review.
"""

import json
import logging
from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)


def screen_experienced(candidate: dict, jd_requirements: dict) -> dict:
    """Apply the full 8-step experienced candidate screening SOP.

    Args:
        candidate: Parsed resume profile from resume_parser.
        jd_requirements: Skills matrix / JD requirements.

    Returns:
        Full screening result with weighted scores and verdict.
    """
    logger.info(f"Experienced screening: {candidate.get('name', 'unknown')}")

    prompt = f"""You are an expert HR professional screening an EXPERIENCED technical candidate.
Follow this EXACT 8-step procedure. Focus on depth of work, contributions, career trajectory, and impact.
Degrees matter LESS. What the candidate has built, solved, or led matters MOST.

=== JD REQUIREMENTS ===
Role: {jd_requirements.get('role_title', 'unknown')}
Min experience: {jd_requirements.get('experience_level', '3+ years')}
Must-have skills: {[s.get('skill', s) if isinstance(s, dict) else s for s in jd_requirements.get('must_have', [])]}
Nice-to-have skills: {[s.get('skill', s) if isinstance(s, dict) else s for s in jd_requirements.get('nice_to_have', [])]}
Required tech stack: {jd_requirements.get('frameworks', [])}

=== CANDIDATE RESUME (JSON) ===
{json.dumps({
    "name": candidate.get("name", "unknown"),
    "total_experience_years": candidate.get("total_experience_years", 0),
    "work_history": candidate.get("work_history", []),
    "skills_evidenced": candidate.get("skills", {}).get("evidenced", []) if isinstance(candidate.get("skills"), dict) else [],
    "skills_claimed_only": candidate.get("skills", {}).get("claimed_only", []) if isinstance(candidate.get("skills"), dict) else [],
    "projects": candidate.get("projects", []),
    "education": candidate.get("education", []),
    "certifications": candidate.get("certifications", []),
    "repo_urls": candidate.get("repo_urls", []),
    "online_presence": candidate.get("online_presence", {}),
    "resume_quality": candidate.get("resume_quality", {}),
}, indent=2, default=str)}

=== PROCEDURE ===

STEP 1 — RESUME STRUCTURE:
- Clear summary/objective? Reverse chronological? Labeled sections?
- 1-3 pages appropriate for experienced? Cluttered/inconsistent?
- A poorly structured senior resume is a signal — note it but don't reject solely on this.

STEP 2 — VERIFY RELEVANT EXPERIENCE:
- Sum only directly relevant roles. Exclude internships/part-time unless explicitly relevant.
- Note employment gaps (don't reject). Compare against JD minimum.
- JD needs 5+ and candidate has 4.5 strong years → borderline, flag (don't reject).

STEP 3 — DEEP-DIVE WORK EXPERIENCE (most critical — spend most time here):
3a) JOB TITLES: Progressive (Junior→Senior)? Stagnant = concern. Rapid promotion = strong.
3b) ROLE DEPTH: Specific measurable descriptions vs vague "worked on backend."
   Strong: "Designed microservices on AWS handling 1M+ daily requests"
   Weak: "Worked on backend development"
3c) OWNERSHIP: Look for led, architected, owned, drove, built from scratch, mentored.
   Only "assisted"/"supported" throughout → may lack initiative.
3d) QUANTIFIED IMPACT: Numbers and outcomes.
   "Reduced API response time by 40%" "Led team of 6" "Saved $50K annually"
   No measurable impact across multiple roles → weak signal.
3e) TECH STACK: List all tech per role. Cross-reference with JD.
   Used-in-project weighted HIGHER than listed-in-skills-section.

STEP 4 — TECHNICAL SKILLS (with caution):
- For each key skill, verify evidence in work history.
- Unverified → "claimed, unverified"
- Check recency: still active or 6+ years ago?
- 30+ skills ≠ stronger than 10 well-evidenced skills.

STEP 5 — PROJECTS / OPEN SOURCE:
- Links → strong positive. For 5+ years: end-to-end ownership?
- Open source, packages, tech blogs → strong additional signals.
- Don't penalize absence — but reward presence.

STEP 6 — EDUCATION / CERTIFICATIONS (secondary):
- Strong work record outweighs non-traditional education.
- Certs > 3 years old in fast domains = less weight.
- "8 years proven + no degree" often > "degree + 3 years unfocused"

STEP 7 — CAREER STABILITY:
- Average tenure per company. < 1 year consistently = red flag.
- Pattern of 6-month tenures across 5+ companies = flag.
- 1-2 short stints acceptable. Industry switches with transferable skills = ok.

STEP 8 — FINAL VERDICT:
Weighted scoring:
  Depth of work experience: 30%
  Technical skills match:   25%
  Career trajectory:        20%
  Projects / contributions: 15%
  Education / certifications: 10%

Score ranges:
  75-100: Shortlisted — Strong Fit
  55-74:  Shortlisted — Good Fit
  40-54:  Flagged for Review (NEVER auto-reject)
  Below 40: Not Shortlisted

Return JSON:
{{
    "procedure": "experienced_8step",
    "experience_verification": {{
        "total_years_claimed": {candidate.get('total_experience_years', 0)},
        "relevant_years_verified": 0,
        "meets_jd_minimum": true,
        "gaps_noted": [],
        "borderline": false
    }},
    "work_experience_analysis": {{
        "title_progression": "progressive | stagnant | rapid",
        "role_depth": "strong | moderate | weak",
        "ownership_level": "high | moderate | low",
        "quantified_impact": true,
        "tech_stack_overlap_pct": 70,
        "key_achievements": ["...", "..."],
        "concerns": ["..."]
    }},
    "dimension_scores": {{
        "work_experience_depth": {{"score": 25, "max": 30, "notes": "..."}},
        "technical_skills_match": {{"score": 20, "max": 25, "notes": "...", "verified_count": 6, "claimed_count": 4, "overlap_pct": 70}},
        "career_trajectory": {{"score": 16, "max": 20, "notes": "..."}},
        "projects_contributions": {{"score": 12, "max": 15, "notes": "..."}},
        "education_certifications": {{"score": 8, "max": 10, "notes": "..."}}
    }},
    "total_score": 81,
    "max_score": 100,
    "verdict": "Shortlisted — Strong Fit | Shortlisted — Good Fit | Flagged for Review | Not Shortlisted",
    "red_flags": [],
    "auto_escalate_reasons": [],
    "decision_note": "2-3 line summary with top strengths and specific gaps",
    "escalate_to_human": false,
    "top_strengths": ["...", "..."],
    "key_gaps": ["...", "..."]
}}

AUTOMATIC RED FLAGS — set escalate_to_human=true if ANY of these are detected:
⚠️ Job-hopping: < 1 year at 3+ consecutive companies
⚠️ Unexplained gaps > 6 months
⚠️ Skills listed but completely absent from work history
⚠️ Vague language across all roles with no metrics
⚠️ Seniority mismatch (title vs actual responsibilities)
⚠️ No technical growth or title progression over 5+ years

CRITICAL: When in doubt, ALWAYS flag for human review rather than hard rejection.
"""

    try:
        result = call_llm_json(prompt)
        result["procedure"] = "experienced_8step"
        result["candidate_name"] = candidate.get("name", "unknown")

        # Ensure auto-escalation logic
        if result.get("auto_escalate_reasons"):
            result["escalate_to_human"] = True

        score = result.get("total_score", 0)
        if 40 <= score < 55:
            result["escalate_to_human"] = True
            if result["verdict"] == "Not Shortlisted":
                result["verdict"] = "Flagged for Review"

        logger.info(
            f"Experienced screening done: {candidate.get('name')} → "
            f"{score}/100 → {result.get('verdict', 'unknown')}"
            f"{' ⚠️ ESCALATED' if result.get('escalate_to_human') else ''}"
        )
        return result

    except Exception as e:
        logger.error(f"Experienced screening failed: {e}")
        return {
            "procedure": "experienced_8step",
            "candidate_name": candidate.get("name", "unknown"),
            "total_score": 0,
            "verdict": "Flagged for Review",
            "escalate_to_human": True,
            "red_flags": [f"Screening error: {str(e)}"],
            "decision_note": f"Automated screening failed — escalated for human review. Error: {e}",
            "error": str(e),
        }
