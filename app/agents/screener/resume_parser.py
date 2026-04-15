"""Resume parser — extract text from PDF/DOCX, then LLM structures it."""

import logging
import os
import tempfile
import uuid
from pathlib import Path

from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)

# Upload directory
UPLOAD_DIR = Path("./uploads/resumes")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using PyMuPDF."""
    try:
        import fitz  # PyMuPDF

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        doc = fitz.open(tmp_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        os.unlink(tmp_path)

        return text.strip()
    except ImportError:
        logger.warning("PyMuPDF not installed — falling back to LLM-only parsing")
        return ""
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def save_resume(file_bytes: bytes, filename: str) -> str:
    """Save resume file and return the path."""
    file_id = str(uuid.uuid4())[:8]
    safe_name = f"{file_id}_{filename}"
    path = UPLOAD_DIR / safe_name
    path.write_bytes(file_bytes)
    return str(path)


def parse_resume(file_bytes: bytes, filename: str, jd_skills: list[str] | None = None) -> dict:
    """Parse a resume into structured candidate profile.

    Returns the full extraction schema from the plan.
    """
    logger.info(f"Parsing resume: {filename} ({len(file_bytes)} bytes)")

    # Step 1: Extract raw text
    raw_text = extract_text_from_pdf(file_bytes)
    if not raw_text:
        # Fallback: send raw bytes description to LLM
        raw_text = f"[Binary file: {filename}, {len(file_bytes)} bytes — could not extract text]"

    # Step 2: LLM structures the extracted text
    skills_context = ""
    if jd_skills:
        skills_context = f"\nJD Required Skills (for matching): {', '.join(jd_skills)}"

    prompt = f"""Extract structured data from this resume text. Be thorough and accurate.
Score only what is clearly written — never assume or inflate.
{skills_context}

Resume Text:
---
{raw_text[:6000]}
---

Return JSON with this exact structure:
{{
    "name": "Full Name",
    "email": "email@example.com",
    "phone": "+91-XXXXXXXXXX or null",
    "education": [
        {{
            "degree": "B.Tech Computer Science",
            "institution": "Institution Name",
            "year": 2024,
            "gpa": "8.5/10 or 85% or null",
            "honors": ["Dean's List", "Gold Medal"] 
        }}
    ],
    "work_history": [
        {{
            "company": "Company Name",
            "role": "Job Title",
            "duration_months": 18,
            "start_year": 2022,
            "end_year": 2024,
            "is_current": false,
            "skills_used": ["React", "TypeScript"],
            "impact_statements": ["Reduced load time by 40%"],
            "ownership_signals": ["led", "architected"],
            "description": "Brief role description"
        }}
    ],
    "total_experience_years": 2.5,
    "skills": {{
        "evidenced": ["React", "Next.js"],
        "claimed_only": ["Docker", "Kubernetes"],
        "all_listed": ["React", "Next.js", "Docker", "Kubernetes"]
    }},
    "projects": [
        {{
            "name": "Project Name",
            "description": "What it does",
            "tech": ["Next.js", "Prisma"],
            "url": "https://github.com/... or null",
            "is_self_initiated": true,
            "has_live_demo": false
        }}
    ],
    "repo_urls": ["https://github.com/username"],
    "internships": [
        {{
            "company": "Company",
            "role": "Intern Title",
            "duration_months": 3,
            "domain_relevant": true,
            "mode": "remote",
            "description": "Tasks performed"
        }}
    ],
    "certifications": [
        {{
            "name": "AWS Cloud Practitioner",
            "platform": "AWS",
            "year": 2025,
            "relevant": true
        }}
    ],
    "initiative_signals": ["hackathon_winner", "github_active", "technical_blog"],
    "leadership_roles": ["Club President", "Event Organizer"],
    "online_presence": {{
        "linkedin": "url or null",
        "github": "url or null",
        "portfolio": "url or null",
        "blog": "url or null"
    }},
    "resume_quality": {{
        "has_clear_sections": true,
        "has_contact_info": true,
        "page_count_estimate": 1,
        "spelling_grammar_issues": false,
        "well_organized": true
    }},
    "extraction_confidence": 0.89
}}

Important:
- For skills, classify as "evidenced" ONLY if the skill appears in a project, job, or internship description.
- Skills listed only in a skills section with no supporting context are "claimed_only".
- Set total_experience_years by summing work_history durations (exclude internships).
- extraction_confidence: 0.9+ if text was clean, 0.7-0.9 if some parts unclear, <0.7 if poor quality.
"""

    try:
        profile = call_llm_json(prompt)
        profile["_raw_text_length"] = len(raw_text)
        profile["_filename"] = filename
        logger.info(
            f"Parsed: {profile.get('name', 'unknown')}, "
            f"{profile.get('total_experience_years', 0)} years, "
            f"{len(profile.get('skills', {}).get('evidenced', []))} evidenced skills, "
            f"confidence={profile.get('extraction_confidence', 0)}"
        )
        return profile
    except Exception as e:
        logger.error(f"Resume parsing LLM failed: {e}")
        return {
            "name": "Parse Error",
            "email": None,
            "error": str(e),
            "extraction_confidence": 0.0,
            "_raw_text_length": len(raw_text),
            "_filename": filename,
        }
