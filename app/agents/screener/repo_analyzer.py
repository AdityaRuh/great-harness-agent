"""Candidate repo analyzer — clone public repos, score quality + tech overlap."""

import logging
import os
import shutil
import subprocess
import tempfile

from app.integrations.llm import call_llm_json

logger = logging.getLogger(__name__)


def analyze_candidate_repo(repo_url: str, tech_stack_profile: dict) -> dict:
    """Clone a public repo and analyze it for hiring evaluation.

    No auth needed — candidate repos are public.
    """
    logger.info(f"Analyzing repo: {repo_url}")

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="candidate_repo_")

        # Clone (shallow, with timeout)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, temp_dir],
            capture_output=True, timeout=120, text=True,
        )
        if result.returncode != 0:
            logger.warning(f"Clone failed: {result.stderr[:200]}")
            return {"repo_url": repo_url, "repo_score": 0, "error": "clone_failed"}

        # Collect metrics
        metrics = _collect_repo_metrics(temp_dir)

        # Sample code files for LLM analysis
        samples = _sample_code_files(temp_dir, max_files=8, max_chars_per_file=1500)

        # LLM scores the repo
        score = _llm_score_repo(repo_url, metrics, samples, tech_stack_profile)
        score["repo_url"] = repo_url

        logger.info(f"Repo scored: {repo_url} → {score.get('repo_score', 0)}/100")
        return score

    except subprocess.TimeoutExpired:
        logger.warning(f"Repo clone timed out: {repo_url}")
        return {"repo_url": repo_url, "repo_score": 0, "error": "timeout"}
    except Exception as e:
        logger.error(f"Repo analysis failed: {e}")
        return {"repo_url": repo_url, "repo_score": 0, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def _collect_repo_metrics(repo_path: str) -> dict:
    """Collect basic repo metrics without LLM."""
    extensions = {}
    total_files = 0
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden dirs and node_modules
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"
                   and d != "__pycache__" and d != "dist" and d != "build"]
        for f in files:
            total_files += 1
            ext = os.path.splitext(f)[1].lower()
            if ext:
                extensions[ext] = extensions.get(ext, 0) + 1

    has_tests = any(
        os.path.isdir(os.path.join(repo_path, d))
        for d in ["tests", "__tests__", "test", "spec", "specs"]
    )
    has_readme = os.path.isfile(os.path.join(repo_path, "README.md"))
    has_ci = os.path.isdir(os.path.join(repo_path, ".github", "workflows"))
    has_dockerfile = os.path.isfile(os.path.join(repo_path, "Dockerfile"))
    has_gitignore = os.path.isfile(os.path.join(repo_path, ".gitignore"))
    has_package_json = os.path.isfile(os.path.join(repo_path, "package.json"))
    has_pyproject = os.path.isfile(os.path.join(repo_path, "pyproject.toml"))

    # Read README if exists
    readme_content = ""
    if has_readme:
        try:
            readme_content = open(os.path.join(repo_path, "README.md")).read()[:2000]
        except Exception:
            pass

    return {
        "total_files": total_files,
        "extensions": dict(sorted(extensions.items(), key=lambda x: -x[1])[:10]),
        "has_tests": has_tests,
        "has_readme": has_readme,
        "has_ci": has_ci,
        "has_dockerfile": has_dockerfile,
        "has_gitignore": has_gitignore,
        "has_package_json": has_package_json,
        "has_pyproject": has_pyproject,
        "readme_preview": readme_content[:500],
    }


def _sample_code_files(repo_path: str, max_files: int = 8, max_chars_per_file: int = 1500) -> list:
    """Sample representative code files for LLM analysis."""
    code_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs"}
    samples = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   {"node_modules", "__pycache__", "dist", "build", ".next", "venv", ".venv"}]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in code_extensions:
                fpath = os.path.join(root, f)
                try:
                    content = open(fpath).read()[:max_chars_per_file]
                    rel_path = os.path.relpath(fpath, repo_path)
                    samples.append({"file": rel_path, "content": content})
                except Exception:
                    continue
                if len(samples) >= max_files:
                    return samples

    return samples


def _llm_score_repo(repo_url: str, metrics: dict, samples: list, tech_stack: dict) -> dict:
    """Use LLM to score repo quality and tech overlap."""
    prompt = f"""Score this candidate's GitHub repository for a hiring evaluation.

Repo: {repo_url}

Metrics:
- Total files: {metrics['total_files']}
- File types: {metrics['extensions']}
- Has tests: {metrics['has_tests']}
- Has README: {metrics['has_readme']}
- Has CI/CD: {metrics['has_ci']}
- Has Docker: {metrics['has_dockerfile']}

README preview: {metrics.get('readme_preview', 'none')[:300]}

Code samples ({len(samples)} files):
{chr(10).join(f"--- {s['file']} ---{chr(10)}{s['content'][:800]}" for s in samples[:5])}

Our required tech stack: {tech_stack.get('frameworks', [])} + {tech_stack.get('libraries', [])}

Score 0-100 across these dimensions:
1. Tech stack overlap with our requirements (0-30)
2. Code quality: clean, readable, consistent style, error handling (0-25)
3. Project structure: organized, separation of concerns (0-15)
4. Documentation: README quality, comments (0-15)
5. Engineering practices: tests, CI, Docker, gitignore (0-15)

Return JSON:
{{
    "repo_score": 72,
    "tech_overlap": ["React", "TypeScript", "Next.js"],
    "tech_missing": ["Tailwind"],
    "dimension_scores": {{
        "tech_overlap": {{"score": 22, "max": 30}},
        "code_quality": {{"score": 18, "max": 25}},
        "project_structure": {{"score": 12, "max": 15}},
        "documentation": {{"score": 10, "max": 15}},
        "engineering_practices": {{"score": 10, "max": 15}}
    }},
    "strengths": ["Clean TypeScript code", "Good README"],
    "weaknesses": ["No tests", "No CI pipeline"],
    "summary": "1-2 sentence assessment"
}}"""

    try:
        return call_llm_json(prompt)
    except Exception as e:
        logger.error(f"LLM repo scoring failed: {e}")
        return {
            "repo_score": 0,
            "error": str(e),
            "tech_overlap": [],
            "strengths": [],
            "weaknesses": ["Could not analyze"],
        }
