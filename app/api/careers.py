"""Public careers page with application form + auto-screening.

Candidate applies → resume saved → auto-added to pipeline → Agent 2 screens immediately.
Results appear on HR dashboard automatically.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.pipelines import get_graph, _pipelines
from app.storage import get_pipeline as storage_get_pipeline
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(tags=["Careers"])

UPLOAD_DIR = "./uploads/applications"
os.makedirs(UPLOAD_DIR, exist_ok=True)

_applications: dict[str, list] = {}


@router.get("/careers", response_class=HTMLResponse)
async def careers_listing():
    graph = get_graph()
    jobs = []
    for pid, pdata in _pipelines.items():
        config = {"configurable": {"thread_id": pid}}
        try:
            state = await graph.aget_state(config)
            if not state or not state.values: continue
            s = state.values
            if s.get("jd_approved") and s.get("jd_draft"):
                jobs.append({"id": pid, "title": s.get("role_title", ""), "department": pdata["config"].get("department", "Engineering"), "experience": pdata["config"].get("experience_level", "")})
        except Exception: continue

    cards = "".join(f'<a href="/careers/{j["id"]}" class="job-card"><h3>{j["title"]}</h3><div class="meta">{j["department"]} · {j["experience"]}</div><span class="arrow">→</span></a>' for j in jobs)
    if not cards: cards = '<p class="empty">No open positions right now. Check back soon!</p>'
    return HTMLResponse(_page("Open Positions", f'<div class="hero"><h1>Join Our Team</h1><p class="sub">We\'re building the future of AI. Come build it with us.</p></div><div class="jobs">{cards}</div>', listing_css=True))


@router.get("/careers/{pipeline_id}", response_class=HTMLResponse)
async def careers_detail(pipeline_id: str):
    if pipeline_id not in _pipelines:
        # Multi-worker: pipeline might be in another worker's memory — check DB
        pdata = await storage_get_pipeline(pipeline_id)
        if pdata:
            _pipelines[pipeline_id] = pdata  # Cache locally
        else:
            raise HTTPException(404, "Not found")
    graph = get_graph()
    state = await graph.aget_state({"configurable": {"thread_id": pipeline_id}})
    if not state or not state.values or not state.values.get("jd_draft"): raise HTTPException(404, "Not found")
    s = state.values
    role = s.get("role_title", "Open Position")
    dept = _pipelines[pipeline_id]["config"].get("department", "Engineering")
    exp = _pipelines[pipeline_id]["config"].get("experience_level", "")
    jd_html = _md(s["jd_draft"])

    return HTMLResponse(_page(role, f'''
<article class="jd">
<div class="jd-header"><div class="badge">{dept} · {exp}</div><h1>{role}</h1>
<button class="apply-btn" onclick="document.getElementById('apply-modal').style.display='flex'">Apply Now</button></div>
<div class="jd-body">{jd_html}</div>
<div class="jd-footer"><button class="apply-btn" onclick="document.getElementById('apply-modal').style.display='flex'">Apply for this Role</button>
<a href="/careers" class="back-link">← View All Positions</a></div>
</article>

<div id="apply-modal" class="modal">
<div class="modal-content">
<button class="modal-close" onclick="document.getElementById('apply-modal').style.display='none'">✕</button>
<div class="modal-header"><div class="modal-label">APPLY FOR JOB:</div><h2 class="modal-title">{role}</h2><div class="modal-company">AT RUH AI</div></div>
<hr class="modal-divider">
<form id="apply-form" onsubmit="submitApp(event,'{pipeline_id}')">
<div class="fg"><label>Your name <span class="req">*</span></label><input type="text" name="name" placeholder="Your Name" required></div>
<div class="fg"><label>Your email address <span class="req">*</span></label><input type="email" name="email" placeholder="Your Email" required></div>
<div class="fg"><label>Phone <span class="req">*</span></label><input type="tel" name="phone" placeholder="Your Phone" required></div>
<div class="fr">
<div class="fg"><label>Years of Experience <span class="req">*</span></label><input type="number" name="experience_years" placeholder="e.g. 3.5" step="0.5" required><small>years</small></div>
<div class="fg"><label>Current CTC <span class="req">*</span></label><input type="number" name="current_ctc" placeholder="e.g. 12.5" step="0.5" required><small>Lakhs per annum</small></div>
<div class="fg"><label>Notice Period <span class="req">*</span></label><select name="notice_period" required><option value="">Select</option><option>Immediate</option><option>15 days</option><option>30 days</option><option>60 days</option><option>90 days</option></select></div>
</div>
<div class="fg"><label>Message</label><textarea name="message" rows="3" placeholder="Tell us a bit about yourself..."></textarea></div>
<div class="fg"><label>Resume / CV <span class="req">*</span></label>
<div class="upload-zone" onclick="document.getElementById('ri').click()"><div class="ui">⬆</div><div>Click to upload or drag and drop</div><small>PDF, DOC, DOCX up to 10MB</small></div>
<input type="file" id="ri" name="resume" accept=".pdf,.doc,.docx" required style="display:none" onchange="this.previousElementSibling.innerHTML='<div style=color:#00D68F>✓ '+this.files[0].name+'</div>'"></div>
<div class="fg"><label>LinkedIn Profile</label><input type="url" name="linkedin" placeholder="linkedin.com/in/username"></div>
<div class="fg"><label>How did you find out about us?</label><select name="source"><option value="">Select an option</option><option>LinkedIn</option><option>Indeed</option><option>Referral</option><option>Company Website</option><option>Other</option></select></div>
<div class="fg" style="display:flex;align-items:flex-start;gap:10px"><input type="checkbox" name="consent" id="consent" style="width:auto;margin-top:4px"><label for="consent" style="font-size:13px;color:#6B7394">I consent to receiving from the Company e-mail information about new job offers, our events, trainings and other ongoing activities.</label></div>
<div id="fs"></div>
<button type="submit" class="submit-btn" id="sb">Submit</button>
<div id="screening-status" style="margin-top:12px"></div>
</form></div></div>
<script>
async function submitApp(e,pid){{e.preventDefault();const b=document.getElementById('sb');b.disabled=true;b.textContent='Submitting & Screening...';
document.getElementById('screening-status').innerHTML='<div style="color:#4ECDC4;font-size:13px;padding:8px 0">⏳ Uploading resume and running AI screening... this takes ~60 seconds</div>';
const fd=new FormData(document.getElementById('apply-form'));const r=document.getElementById('ri').files[0];if(r)fd.set('resume',r);
try{{const res=await fetch('/careers/'+pid+'/apply',{{method:'POST',body:fd}});const d=await res.json();
if(d.success){{
let msg='<div style="color:#00D68F;font-size:15px;font-weight:600;padding:16px 0">✓ Application submitted successfully!</div>';
if(d.screening){{msg+='<div style="background:#12151B;border:1px solid #1E2230;border-radius:12px;padding:16px;margin-top:12px;color:#E8ECF4">';
msg+='<div style="font-size:13px;color:#6B7394;margin-bottom:8px">AI Screening Result:</div>';
msg+='<div style="font-size:24px;font-weight:700;color:'+(d.screening.score>=60?'#00D68F':d.screening.score>=45?'#FFB344':'#FF6B6B')+'">'+d.screening.score+'/100</div>';
msg+='<div style="font-size:14px;font-weight:600;margin-top:4px;color:#E8ECF4">'+d.screening.verdict+'</div>';
msg+='</div>';}}
else{{msg+='<div style="color:#6B7394;font-size:13px;margin-top:8px">We\\'ll review your profile and get back to you soon.</div>';}}
document.getElementById('fs').innerHTML=msg;document.getElementById('apply-form').querySelector('.submit-btn').style.display='none';document.getElementById('screening-status').innerHTML='';}}
else{{document.getElementById('fs').innerHTML='<div style="color:#FF6B6B;padding:8px 0">Error: '+(d.error||'Failed')+'</div>';b.disabled=false;b.textContent='Submit';document.getElementById('screening-status').innerHTML='';}}
}}catch(err){{document.getElementById('fs').innerHTML='<div style="color:#FF6B6B;padding:8px 0">Network error</div>';b.disabled=false;b.textContent='Submit';document.getElementById('screening-status').innerHTML='';}}}}
</script>''', form_css=True))


@router.post("/careers/{pipeline_id}/apply")
async def apply(pipeline_id: str, name: str = Form(...), email: str = Form(...), phone: str = Form(...),
    experience_years: float = Form(...), current_ctc: float = Form(...), notice_period: str = Form(...),
    message: str = Form(""), linkedin: str = Form(""), source: str = Form(""), consent: str = Form(""),
    resume: UploadFile = File(...)):
    """Receive application and auto-screen with Agent 2."""
    if pipeline_id not in _pipelines:
        return JSONResponse({"success": False, "error": "Not found"}, 404)

    # 1. Save resume to disk
    rid = str(uuid.uuid4())[:8]
    path = os.path.join(UPLOAD_DIR, f"{rid}_{resume.filename}")
    file_bytes = await resume.read()
    with open(path, "wb") as f:
        f.write(file_bytes)

    # 2. Store application metadata
    app_data = {
        "id": rid, "pipeline_id": pipeline_id, "name": name, "email": email,
        "phone": phone, "experience_years": experience_years, "current_ctc": current_ctc,
        "notice_period": notice_period, "message": message, "linkedin": linkedin,
        "source": source, "resume_path": path, "resume_filename": resume.filename,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    _applications.setdefault(pipeline_id, []).append(app_data)
    logger.info(f"Application: {name} ({email}) for {pipeline_id[:8]}")

    # 3. Auto-screen with Agent 2 in thread pool (non-blocking)
    screening_result = None
    try:
        from app.agents.screener.resume_parser import parse_resume
        from app.agents.screener.fresher_screener import screen_fresher
        from app.agents.screener.experienced_screener import screen_experienced

        # Get JD skills from pipeline state
        graph = get_graph()
        config = {"configurable": {"thread_id": pipeline_id}}
        state = await graph.aget_state(config)
        jd_skills = []
        jd_requirements = {}
        if state and state.values:
            sm = state.values.get("skills_matrix", {})
            raw = sm.get("must_have", []) + sm.get("nice_to_have", [])
            jd_skills = [str(list(s.values())[0]) if isinstance(s, dict) else str(s) for s in raw]
            jd_requirements = sm

        # Step 1: Parse resume (non-blocking)
        logger.info(f"Parsing resume for {name}: {resume.filename}")
        parsed = await asyncio.to_thread(parse_resume, file_bytes, resume.filename, jd_skills)

        # Step 2: Determine fresher vs experienced
        candidate_type = "fresher" if experience_years < 3 else "experienced"
        candidate = {**parsed, "candidate_type": candidate_type, "name": name, "email": email, "total_experience_years": experience_years}

        # Step 3: Screen with appropriate SOP (non-blocking)
        logger.info(f"Screening {name} as {candidate_type}")
        if candidate_type == "fresher":
            result = await asyncio.to_thread(screen_fresher, candidate, jd_requirements)
        else:
            result = await asyncio.to_thread(screen_experienced, candidate, jd_requirements)

        screening_result = {
            "score": result.get("total_score", 0),
            "verdict": result.get("verdict", "Pending"),
            "strengths": result.get("top_strengths", []),
        }

        # Store in pipeline state for dashboard visibility
        if state and state.values:
            candidates = state.values.get("candidates", [])
            candidates.append({
                "candidate": {
                    **parsed,
                    "name": name,
                    "email": email,
                    "total_experience_years": experience_years,
                },
                "screening_result": result,
                "candidate_type": candidate_type,
                "application_data": {"name": name, "email": email, "phone": phone,
                    "experience_years": experience_years, "current_ctc": current_ctc,
                    "notice_period": notice_period, "linkedin": linkedin},
            })
            from langgraph.types import Command
            async for _ in graph.astream(Command(update={"candidates": candidates}), config, stream_mode="values"):
                pass

        logger.info(f"Screening complete for {name}: {screening_result}")

    except Exception as e:
        logger.error(f"Auto-screening failed for {name}: {e}", exc_info=True)

    return JSONResponse({
        "success": True,
        "application_id": rid,
        "message": f"Thank you {name}!",
        "screening": screening_result,
    })


@router.get("/api/v1/pipelines/{pipeline_id}/applications")
async def get_applications(pipeline_id: str):
    return {"pipeline_id": pipeline_id, "total": len(_applications.get(pipeline_id, [])),
            "applications": _applications.get(pipeline_id, [])}


def _md(md):
    import re
    h = md
    h = re.sub(r'^### (.+)$', r'<h3>\1</h3>', h, flags=re.MULTILINE)
    h = re.sub(r'^## (.+)$', r'<h2>\1</h2>', h, flags=re.MULTILINE)
    h = re.sub(r'^# (.+)$', r'<h1>\1</h1>', h, flags=re.MULTILINE)
    h = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', h)
    h = re.sub(r'\*(.+?)\*', r'<em>\1</em>', h)
    lines, result, il = h.split("\n"), [], False
    for l in lines:
        s = l.strip()
        if s.startswith("- ") or s.startswith("* "):
            if not il: result.append("<ul>"); il = True
            result.append(f"<li>{s[2:]}</li>")
        else:
            if il: result.append("</ul>"); il = False
            if s and not s.startswith("<"): result.append(f"<p>{s}</p>")
            elif s: result.append(s)
    if il: result.append("</ul>")
    return "\n".join(result)


def _page(title, content, listing_css=False, form_css=False):
    extra = ""
    if listing_css:
        extra += ".jobs{max-width:700px;margin:0 auto;display:grid;gap:12px}.job-card{display:flex;align-items:center;padding:20px 24px;background:#16181E;border:1px solid #1E2230;border-radius:12px;text-decoration:none;color:#E8ECF4;transition:all .2s}.job-card:hover{border-color:#6C5CE7;transform:translateX(4px)}.job-card h3{flex:1;font-size:16px;margin:0}.job-card .meta{font-size:13px;color:#6B7394;margin-right:16px}.job-card .arrow{font-size:18px;color:#6C5CE7}.empty{text-align:center;color:#6B7394;padding:60px;font-size:16px}"
    if form_css:
        extra += ".modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:flex-start;padding:40px 20px;overflow-y:auto}.modal-content{background:#fff;border-radius:16px;max-width:640px;width:100%;padding:40px;position:relative;color:#1a1a2e}.modal-close{position:absolute;top:16px;right:16px;background:none;border:none;font-size:20px;cursor:pointer;color:#666}.modal-header{text-align:center}.modal-label{font-size:12px;letter-spacing:1px;color:#666;text-transform:uppercase;margin-bottom:8px}.modal-title{font-size:22px;font-weight:700;color:#6C5CE7;margin:8px 0 4px}.modal-company{font-size:14px;font-weight:700;color:#1a1a2e}.modal-divider{border:none;border-top:1px solid #eee;margin:20px 0}.fg{margin-bottom:20px}.fg label{display:block;font-size:14px;color:#444;margin-bottom:6px;font-weight:500}.fg input,.fg textarea,.fg select{width:100%;padding:12px 16px;font-size:15px;border:1px solid #ddd;border-radius:8px;font-family:'DM Sans',sans-serif;background:#fff;color:#1a1a2e;outline:none}.fg input:focus,.fg textarea:focus,.fg select:focus{border-color:#6C5CE7}.fg small{font-size:12px;color:#999;margin-top:4px;display:block}.fr{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}.req{color:#6C5CE7}.upload-zone{border:2px dashed #ddd;border-radius:12px;padding:30px;text-align:center;cursor:pointer;color:#666;font-size:14px}.upload-zone:hover{border-color:#6C5CE7}.ui{font-size:24px;margin-bottom:8px;opacity:0.5}.submit-btn{display:block;padding:14px 40px;background:#6C5CE7;color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;margin-top:8px}.submit-btn:hover{background:#7D6FF0}.submit-btn:disabled{opacity:0.5}"
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Careers</title><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'DM Sans',sans-serif;background:#0B0D11;color:#E8ECF4;line-height:1.7}}
.nav{{padding:16px 32px;border-bottom:1px solid #1E2230;display:flex;align-items:center}}.nav-brand{{font-size:15px;font-weight:700;color:#fff;text-decoration:none;display:flex;align-items:center;gap:10px}}
.nav-brand span{{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#6C5CE7,#A29BFE);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;color:#fff}}
.container{{max-width:800px;margin:0 auto;padding:40px 24px}}.hero{{text-align:center;padding:48px 0 40px}}.hero h1{{font-size:36px;font-weight:700;margin-bottom:12px}}.hero .sub{{font-size:18px;color:#6B7394}}
.jd-header{{text-align:center;padding:48px 0 32px;border-bottom:1px solid #1E2230;margin-bottom:32px}}.badge{{display:inline-block;padding:4px 14px;font-size:12px;font-weight:600;color:#A29BFE;background:rgba(108,92,231,0.1);border:1px solid rgba(108,92,231,0.25);border-radius:20px;margin-bottom:16px}}
.jd-header h1{{font-size:32px;margin-bottom:24px}}.apply-btn{{display:inline-block;padding:12px 32px;background:#6C5CE7;color:#fff;border:none;border-radius:8px;font-weight:600;font-size:15px;cursor:pointer;font-family:'DM Sans',sans-serif}}.apply-btn:hover{{background:#7D6FF0}}
.jd-body{{padding:0 0 40px}}.jd-body h1,.jd-body h2,.jd-body h3{{margin:28px 0 12px;color:#fff}}.jd-body h2{{font-size:20px}}.jd-body h3{{font-size:17px}}.jd-body p{{color:#9BA3C0;margin-bottom:12px}}.jd-body ul{{padding-left:20px;margin-bottom:16px}}.jd-body li{{color:#9BA3C0;margin-bottom:6px}}.jd-body strong{{color:#E8ECF4}}
.jd-footer{{text-align:center;padding:40px 0;border-top:1px solid #1E2230}}.back-link{{display:block;margin-top:16px;color:#6B7394;text-decoration:none;font-size:14px}}
{extra}</style></head><body><nav class="nav"><a href="/careers" class="nav-brand"><span>G</span> Careers</a></nav><div class="container">{content}</div></body></html>'''
