"""The Great Harness Agent — LangGraph pipeline definition.

4 agents, 14 sub-nodes, 4 human-in-the-loop checkpoints.
Phase 1: Agent 1 (JD Manager) fully functional.
Phase 2-3: Agents 2-4 are placeholder nodes.
"""

import logging
import os
from langgraph.graph import StateGraph, END

from app.graph.state import PipelineState
from app.config import get_settings

# Agent 1 nodes
from app.graph.nodes.agent1_jd import (
    agent1_analyze_repos,
    agent1_map_skills,
    agent1_draft_jd,
    agent1_serialize_and_publish,
)

# Checkpoint nodes
from app.graph.nodes.checkpoints import (
    checkpoint_jd_approval,
    checkpoint_ranking_review,
    checkpoint_shortlist,
    checkpoint_verdict,
)

# Agent 2-4 placeholder nodes
from app.graph.nodes.agent2_screen import (
    agent2_parse_resumes,
    agent2_screen_candidates,
    agent2_rank_candidates,
)
from app.graph.nodes.agent3_interview import (
    agent3_send_invites,
    agent3_generate_questions,
    agent3_run_avatar_interviews,
    agent3_evaluate_responses,
    agent3_send_notifications,
    agent3_aggregate_shortlist,
)
from app.graph.nodes.agent4_schedule import (
    agent4_schedule_interviews,
    agent4_send_briefings,
    agent4_final_emails,
)

logger = logging.getLogger(__name__)


def _route_after_jd_approval(state: PipelineState) -> str:
    """After JD checkpoint: if approved → serialize+publish, else → re-draft."""
    if state.get("jd_approved"):
        return "agent1_serialize_and_publish"
    else:
        return "agent1_draft_jd"  # Re-draft with feedback


def _route_after_ranking(state: PipelineState) -> str:
    if state.get("ranking_approved"):
        return "agent3_send_invites"
    return END  # HR rejected rankings — pipeline ends


def _route_after_shortlist(state: PipelineState) -> str:
    if state.get("shortlist_approved"):
        return "agent4_schedule_interviews"
    return END


def _route_after_verdict(state: PipelineState) -> str:
    return "agent4_final_emails"


def build_pipeline(checkpointer=None):
    """Build and compile the full hiring pipeline graph."""

    if checkpointer is None:
        # Use PostgresSaver if DATABASE_URL is set, otherwise MemorySaver
        db_url = os.environ.get("DATABASE_URL", get_settings().database_url)
        if db_url and db_url.startswith("postgresql") and "user:pass@localhost" not in db_url:
            try:
                from psycopg_pool import ConnectionPool
                from langgraph.checkpoint.postgres import PostgresSaver
                # PostgresSaver needs a psycopg v3 connection string
                sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
                if not sync_url.startswith("postgresql://"):
                    sync_url = db_url
                # Remove channel_binding param if present
                import re as _re
                sync_url = _re.sub(r'[&?]channel_binding=[^&]*', '', sync_url)
                if sync_url.startswith("postgresql://"):
                    pool = ConnectionPool(conninfo=sync_url, min_size=1, max_size=3)
                    checkpointer = PostgresSaver(pool)
                    checkpointer.setup()
                    logger.info("Using PostgresSaver for graph checkpoints")
                else:
                    from langgraph.checkpoint.memory import MemorySaver
                    checkpointer = MemorySaver()
                    logger.info("Using MemorySaver (fallback)")
            except Exception as e:
                logger.warning(f"PostgresSaver failed, using MemorySaver: {e}")
                from langgraph.checkpoint.memory import MemorySaver
                checkpointer = MemorySaver()
        else:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
            logger.info("Using MemorySaver (no DATABASE_URL configured)")

    builder = StateGraph(PipelineState)

    # ── Agent 1: JD Manager ─────────────────────────────────────
    builder.add_node("agent1_analyze_repos", agent1_analyze_repos)
    builder.add_node("agent1_map_skills", agent1_map_skills)
    builder.add_node("agent1_draft_jd", agent1_draft_jd)
    builder.add_node("checkpoint_jd_approval", checkpoint_jd_approval)
    builder.add_node("agent1_serialize_and_publish", agent1_serialize_and_publish)

    # ── Agent 2: Screener ───────────────────────────────────────
    builder.add_node("agent2_parse_resumes", agent2_parse_resumes)
    builder.add_node("agent2_screen_candidates", agent2_screen_candidates)
    builder.add_node("agent2_rank_candidates", agent2_rank_candidates)
    builder.add_node("checkpoint_ranking_review", checkpoint_ranking_review)

    # ── Agent 3: Avatar Interviewer ─────────────────────────────
    builder.add_node("agent3_send_invites", agent3_send_invites)
    builder.add_node("agent3_generate_questions", agent3_generate_questions)
    builder.add_node("agent3_run_avatar_interviews", agent3_run_avatar_interviews)
    builder.add_node("agent3_evaluate_responses", agent3_evaluate_responses)
    builder.add_node("agent3_send_notifications", agent3_send_notifications)
    builder.add_node("agent3_aggregate_shortlist", agent3_aggregate_shortlist)
    builder.add_node("checkpoint_shortlist", checkpoint_shortlist)

    # ── Agent 4: Scheduler ──────────────────────────────────────
    builder.add_node("agent4_schedule_interviews", agent4_schedule_interviews)
    builder.add_node("agent4_send_briefings", agent4_send_briefings)
    builder.add_node("checkpoint_verdict", checkpoint_verdict)
    builder.add_node("agent4_final_emails", agent4_final_emails)

    # ── Edges ───────────────────────────────────────────────────

    # Agent 1 flow
    builder.set_entry_point("agent1_analyze_repos")
    builder.add_edge("agent1_analyze_repos", "agent1_map_skills")
    builder.add_edge("agent1_map_skills", "agent1_draft_jd")
    builder.add_edge("agent1_draft_jd", "checkpoint_jd_approval")
    builder.add_conditional_edges("checkpoint_jd_approval", _route_after_jd_approval)
    builder.add_edge("agent1_serialize_and_publish", "agent2_parse_resumes")

    # Agent 2 flow
    builder.add_edge("agent2_parse_resumes", "agent2_screen_candidates")
    builder.add_edge("agent2_screen_candidates", "agent2_rank_candidates")
    builder.add_edge("agent2_rank_candidates", "checkpoint_ranking_review")
    builder.add_conditional_edges("checkpoint_ranking_review", _route_after_ranking)

    # Agent 3 flow
    builder.add_edge("agent3_send_invites", "agent3_generate_questions")
    builder.add_edge("agent3_generate_questions", "agent3_run_avatar_interviews")
    builder.add_edge("agent3_run_avatar_interviews", "agent3_evaluate_responses")
    builder.add_edge("agent3_evaluate_responses", "agent3_send_notifications")
    builder.add_edge("agent3_send_notifications", "agent3_aggregate_shortlist")
    builder.add_edge("agent3_aggregate_shortlist", "checkpoint_shortlist")
    builder.add_conditional_edges("checkpoint_shortlist", _route_after_shortlist)

    # Agent 4 flow
    builder.add_edge("agent4_schedule_interviews", "agent4_send_briefings")
    builder.add_edge("agent4_send_briefings", "checkpoint_verdict")
    builder.add_conditional_edges("checkpoint_verdict", _route_after_verdict)
    builder.add_edge("agent4_final_emails", END)

    # Compile
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("Pipeline graph compiled: 20 nodes, 4 checkpoints")
    return graph
