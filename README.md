# Great Harness Agent

AI-powered hiring pipeline — 4 agents, 14 sub-agents, 4 human checkpoints.

## Quick Start

```bash
# Clone
git clone <repo-url>
cd great-harness-agent

# Set up knowledgebase as git submodule
git submodule add https://github.com/AdityaRuh/ruh-org-knowledgebase.git knowledgebase

# Configure
cp .env.example .env
# Edit .env with your tokens

# Start (with Docker)
docker-compose up

# OR start locally
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## API Usage

### Phase 1 — JD Generation

```bash
# Create a pipeline
curl -X POST http://localhost:8000/api/v1/pipelines \
  -H "Content-Type: application/json" \
  -d '{"role_title": "Frontend Developer", "experience_level": "2 years"}'

# Check status
curl http://localhost:8000/api/v1/pipelines/{id}

# Approve JD checkpoint
curl -X POST http://localhost:8000/api/v1/pipelines/{id}/approve \
  -H "Content-Type: application/json" \
  -d '{"checkpoint": "jd_approval", "decision": "approve"}'

# Request changes
curl -X POST http://localhost:8000/api/v1/pipelines/{id}/approve \
  -H "Content-Type: application/json" \
  -d '{"checkpoint": "jd_approval", "decision": "reject", "feedback": "Add remote option"}'
```

### Phase 2 — Resume Screening

```bash
# Upload resumes (after JD is approved)
curl -X POST http://localhost:8000/api/v1/pipelines/{id}/upload \
  -F "files=@resume1.pdf" \
  -F "files=@resume2.pdf" \
  -F "files=@resume3.pdf"

# List candidates with screening status
curl http://localhost:8000/api/v1/pipelines/{id}/candidates

# Get ranked list (Tier A repo-backed above Tier B)
curl http://localhost:8000/api/v1/pipelines/{id}/rankings

# Approve ranking checkpoint
curl -X POST http://localhost:8000/api/v1/pipelines/{id}/approve \
  -H "Content-Type: application/json" \
  -d '{"checkpoint": "ranking_review", "decision": "approve"}'
```

## Architecture

```
POST /api/v1/pipelines → LangGraph runs Agent 1:
  1. analyze_repos    — reads ruh-org-knowledgebase
  2. map_skills       — must-have vs nice-to-have
  3. draft_jd         — Claude Opus generates JD
  4. [CHECKPOINT 1]   — HR approves via API
  5. serialize+publish — JSON + PDF

POST /api/v1/pipelines/{id}/upload → Agent 2 runs:
  6. parse_resumes    — PyMuPDF + Claude Sonnet extraction
  7. screen_candidates — fresher 10-step OR experienced 8-step SOP
  8. rank_candidates  — Tier A (repo-backed) above Tier B (no repo)
  9. [CHECKPOINT 2]   — HR reviews rankings via API

Screening SOPs:
  Fresher  (< 3 years): 10-step, 7 dimensions, max 100 points
  Experienced (≥ 3yr):  8-step, 5 weighted dimensions, 6 auto red flags
  Rule: NEVER auto-reject 45-59 (fresher) or 40-54 (experienced) — always flag.
```

## Phases

- **Phase 1 (current):** Agent 1 — JD from knowledgebase → checkpoint → publish
- **Phase 2:** Agent 2 — Resume screening (fresher 10-step + experienced 8-step SOPs)
- **Phase 3:** Agent 3 — Clawvatar avatar interviews + Agent 4 scheduling
- **Phase 4:** Polish, error handling, load testing
