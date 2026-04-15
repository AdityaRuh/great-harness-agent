"""Great Harness Agent — Streamlit HR Dashboard (optional).

Run separately: streamlit run dashboard.py
Wraps the FastAPI API with a visual interface.

Usage:
  pip install streamlit requests
  streamlit run dashboard.py
"""

import json
import requests
import os

try:
    import streamlit as st
except ImportError:
    print("Install streamlit: pip install streamlit")
    exit(1)

API_URL = os.environ.get("API_URL", "http://localhost:8000/api/v1")

st.set_page_config(page_title="Great Harness Agent", page_icon="🎯", layout="wide")
st.title("🎯 Great Harness Agent")
st.caption("AI-powered hiring pipeline — JD → Screen → Interview → Offer")

# Sidebar
st.sidebar.header("Actions")
action = st.sidebar.radio(
    "Choose action",
    ["Dashboard", "New Pipeline", "View Pipeline", "Upload Resumes", "Approve Checkpoint"],
)


def api_get(path: str):
    try:
        r = requests.get(f"{API_URL}{path}", timeout=10)
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, data=None, files=None):
    try:
        if files:
            r = requests.post(f"{API_URL}{path}", files=files, timeout=30)
        else:
            r = requests.post(f"{API_URL}{path}", json=data, timeout=30)
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


# ── Dashboard ───────────────────────────────────────────────────────
if action == "Dashboard":
    st.header("Pipeline Overview")

    pipelines = api_get("/pipelines")
    if pipelines and isinstance(pipelines, list):
        if not pipelines:
            st.info("No pipelines yet. Create one from the sidebar.")
        else:
            for p in pipelines:
                status_color = {
                    "jd_pending_approval": "🟡",
                    "jd_published": "🟢",
                    "screening_complete": "🔵",
                    "ranking_complete": "🟡",
                    "completed": "✅",
                }.get(p.get("status", ""), "⚪")

                col1, col2, col3 = st.columns([3, 2, 1])
                col1.write(f"**{p.get('role_title', 'Unknown')}**")
                col2.write(f"{status_color} {p.get('status', 'unknown')}")
                col3.button("View", key=p["id"], on_click=lambda pid=p["id"]: st.session_state.update({"view_pipeline": pid}))

    # Metrics
    metrics = api_get("/audit/metrics")
    if metrics:
        st.subheader("Global Metrics")
        col1, col2 = st.columns(2)
        col1.metric("Total Pipelines", metrics.get("total_pipelines", 0))
        col2.json(metrics.get("status_breakdown", {}))


# ── New Pipeline ────────────────────────────────────────────────────
elif action == "New Pipeline":
    st.header("Start New Hiring Pipeline")

    with st.form("new_pipeline"):
        role = st.text_input("Role Title", "Frontend Developer")
        experience = st.text_input("Experience Level", "2 years")
        department = st.text_input("Department", "Engineering")
        candidate_type = st.selectbox("Candidate Type", ["auto", "fresher", "experienced"])
        submitted = st.form_submit_button("Start Pipeline")

    if submitted:
        result = api_post("/pipelines", {
            "role_title": role,
            "experience_level": experience,
            "department": department,
            "candidate_type": candidate_type,
        })
        if result and "id" in result:
            st.success(f"Pipeline started! ID: {result['id']}")
            st.json(result)
        elif result:
            st.error(f"Error: {result}")


# ── View Pipeline ───────────────────────────────────────────────────
elif action == "View Pipeline":
    st.header("View Pipeline")

    pid = st.text_input("Pipeline ID", st.session_state.get("view_pipeline", ""))
    if pid:
        pipeline = api_get(f"/pipelines/{pid}")
        if pipeline:
            st.subheader(f"{pipeline.get('config', {}).get('role_title', 'Unknown')}")

            col1, col2 = st.columns(2)
            col1.metric("Status", pipeline.get("status", "unknown"))
            col2.metric("Checkpoint", pipeline.get("current_checkpoint") or "None")

            # JD Draft
            if pipeline.get("jd_draft"):
                with st.expander("📄 JD Draft", expanded=False):
                    st.markdown(pipeline["jd_draft"])

            # Skills Matrix
            if pipeline.get("skills_matrix"):
                with st.expander("🎯 Skills Matrix", expanded=False):
                    st.json(pipeline["skills_matrix"])

            # Candidates
            candidates = api_get(f"/pipelines/{pid}/candidates")
            if candidates and candidates.get("candidates"):
                st.subheader(f"Candidates ({candidates['total']})")
                for c in candidates["candidates"]:
                    emoji = "✅" if "Shortlisted" in (c.get("screening_verdict") or "") else "⚠️" if "Flagged" in (c.get("screening_verdict") or "") else "❌"
                    st.write(f"{emoji} **{c['name']}** — {c.get('screening_score', '?')}/100 — {c.get('screening_verdict', 'pending')}")

            # Rankings
            rankings = api_get(f"/pipelines/{pid}/rankings")
            if rankings and rankings.get("rankings"):
                st.subheader("Rankings")
                for r in rankings["rankings"]:
                    tier_badge = "🏆" if r["tier"] == "A" else "📋"
                    st.write(f"#{r['rank']} {tier_badge} **{r['name']}** — {r['composite_score']}/100 (Tier {r['tier']})")

            # Audit Log
            audit = api_get(f"/audit/pipelines/{pid}/log")
            if audit and audit.get("entries"):
                with st.expander(f"📋 Audit Log ({audit['total_entries']} entries)", expanded=False):
                    for entry in reversed(audit["entries"][-20:]):
                        st.write(f"**[{entry.get('agent')}]** {entry.get('action')}: {entry.get('explanation', '')[:200]}")


# ── Upload Resumes ──────────────────────────────────────────────────
elif action == "Upload Resumes":
    st.header("Upload Resumes")
    pid = st.text_input("Pipeline ID")
    uploaded = st.file_uploader("Upload PDFs", type=["pdf", "docx"], accept_multiple_files=True)

    if st.button("Upload") and pid and uploaded:
        files = [("files", (f.name, f.read(), "application/pdf")) for f in uploaded]
        result = api_post(f"/pipelines/{pid}/upload", files=files)
        if result:
            st.success(f"Uploaded {result.get('uploaded', 0)} resumes")
            st.json(result)


# ── Approve Checkpoint ──────────────────────────────────────────────
elif action == "Approve Checkpoint":
    st.header("Approve Checkpoint")
    pid = st.text_input("Pipeline ID")
    checkpoint = st.selectbox("Checkpoint", [
        "jd_approval", "ranking_review", "shortlist_confirmation", "final_verdict",
    ])
    decision = st.selectbox("Decision", ["approve", "reject"])
    feedback = st.text_area("Feedback (optional)")

    if st.button("Submit") and pid:
        data = {"checkpoint": checkpoint, "decision": decision}
        if feedback:
            data["feedback"] = feedback
        result = api_post(f"/pipelines/{pid}/approve", data)
        if result:
            st.success(f"Checkpoint resolved: {result.get('new_status', 'unknown')}")
            st.json(result)
