"""Question generator — creates tailored interview questions per candidate.

Uses Claude Opus to generate 20-30 questions covering:
- Warm-up (5): background, projects, motivation
- Technical (10): from skills_matrix, increasing difficulty
- Repo-specific (5): from their actual code (if repos available)
- Behavioral (3): ownership, problem-solving, teamwork
- Wrap-up (2): their questions, availability
"""

import json
import logging
from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)


def generate_interview_questions(
    candidate: dict,
    screening_result: dict,
    skills_matrix: dict,
    tech_stack_profile: dict,
    repo_analysis: dict | None = None,
) -> dict:
    """Generate 25 tailored interview questions for a candidate.

    Returns structured question set with rubrics and follow-ups.
    """
    name = candidate.get("name", "the candidate")
    evidenced_skills = candidate.get("skills", {}).get("evidenced", [])
    claimed_skills = candidate.get("skills", {}).get("claimed_only", [])
    projects = candidate.get("projects", [])
    work_history = candidate.get("work_history", [])
    strengths = screening_result.get("top_strengths", [])
    gaps = screening_result.get("key_gaps", [])

    repo_context = ""
    if repo_analysis and not repo_analysis.get("error"):
        repo_context = f"""
Repo Analysis Available:
- Tech overlap: {repo_analysis.get('tech_overlap', [])}
- Strengths: {repo_analysis.get('strengths', [])}
- Weaknesses: {repo_analysis.get('weaknesses', [])}
Generate 5 repo-specific questions about their actual code.
"""
    else:
        repo_context = "No repo available — skip repo-specific questions, add 5 more technical questions instead."

    prompt = f"""Generate a technical interview question set for this candidate.
These questions will be asked by an AI avatar interviewer — write them in natural spoken language.

=== ROLE ===
{skills_matrix.get('role_title', 'unknown')} — {skills_matrix.get('experience_level', 'unknown')}
Must-have: {[s.get('skill', s) if isinstance(s, dict) else s for s in skills_matrix.get('must_have', [])]}

=== CANDIDATE ===
Name: {name}
Evidenced skills: {evidenced_skills}
Claimed (unverified) skills: {claimed_skills}
Key projects: {json.dumps(projects[:3], default=str)}
Work history: {json.dumps(work_history[:3], default=str)}
Screening strengths: {strengths}
Screening gaps: {gaps}
{repo_context}

=== GENERATE 25 QUESTIONS ===

Return JSON:
{{
    "candidate_name": "{name}",
    "total_questions": 25,
    "estimated_duration_minutes": 20,
    "questions": [
        {{
            "index": 1,
            "category": "warmup | technical | repo_specific | behavioral | wrapup",
            "difficulty": "easy | medium | hard",
            "text": "Natural spoken question the avatar will ask",
            "rubric": {{
                "great": "What a 9-10 answer looks like",
                "good": "What a 6-8 answer looks like",
                "poor": "What a 1-5 answer looks like"
            }},
            "follow_up": "Question to ask if the answer is vague or incomplete",
            "max_time_seconds": 90,
            "skill_being_tested": "React hooks"
        }}
    ]
}}

Rules for question text:
- Write as natural speech, not formal text ("Tell me about..." not "Describe the...")
- Use the candidate's name occasionally for rapport
- Reference their actual projects/work when possible
- Technical questions should test depth, not trivia
- Claimed-only skills deserve probing questions
- For gaps from screening, ask targeted questions to verify
- Difficulty should ramp from easy to hard within technical section
- Each question should have a clear skill_being_tested
- max_time_seconds: 30s for warmup, 60-120s for technical, 120s for behavioral
"""

    try:
        questions = call_llm_json(prompt, model="claude-opus-4-20250514")
        logger.info(
            f"Generated {len(questions.get('questions', []))} questions for {name} "
            f"(est. {questions.get('estimated_duration_minutes', 0)} min)"
        )
        return questions
    except Exception as e:
        logger.error(f"Question generation failed: {e}")
        return _fallback_questions(name, skills_matrix)


def _fallback_questions(name: str, skills_matrix: dict) -> dict:
    """Minimal fallback question set if LLM fails."""
    must_have = [s.get("skill", s) if isinstance(s, dict) else s for s in skills_matrix.get("must_have", [])]

    questions = [
        {"index": 1, "category": "warmup", "text": f"Hi {name}, tell me about yourself and what excites you about this role.",
         "rubric": {"great": "Clear, relevant, enthusiastic", "good": "Adequate", "poor": "Vague"},
         "follow_up": "What specifically drew you to this position?", "max_time_seconds": 60, "skill_being_tested": "communication"},
        {"index": 2, "category": "warmup", "text": "Walk me through a project you're most proud of.",
         "rubric": {"great": "Specific, ownership clear", "good": "Describes project", "poor": "Vague"},
         "follow_up": "What was the hardest part?", "max_time_seconds": 90, "skill_being_tested": "project_experience"},
    ]

    for i, skill in enumerate(must_have[:8], start=3):
        questions.append({
            "index": i, "category": "technical", "text": f"Can you explain how you've used {skill} in a real project?",
            "rubric": {"great": f"Deep {skill} knowledge with examples", "good": "Basic understanding", "poor": "No real experience"},
            "follow_up": f"What was the biggest challenge you faced with {skill}?",
            "max_time_seconds": 90, "skill_being_tested": skill,
        })

    return {"candidate_name": name, "total_questions": len(questions),
            "estimated_duration_minutes": 15, "questions": questions, "fallback": True}
