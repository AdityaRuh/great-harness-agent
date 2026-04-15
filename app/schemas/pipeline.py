"""Pydantic schemas for API requests and responses."""

from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class PipelineCreate(BaseModel):
    role_title: str = Field(..., examples=["Frontend Developer"])
    experience_level: str = Field(..., examples=["2 years"])
    department: str = Field(default="Engineering")
    candidate_type: str = Field(default="auto", description="fresher | experienced | auto")


class PipelineResponse(BaseModel):
    id: str
    status: str
    config: dict
    tech_stack_profile: Optional[dict] = None
    skills_matrix: Optional[dict] = None
    jd_draft: Optional[str] = None
    jd_published_url: Optional[str] = None
    current_checkpoint: Optional[str] = None
    audit_log: list[dict] = []


class CheckpointApproval(BaseModel):
    checkpoint: str = Field(..., description="jd_approval | ranking_review | shortlist_confirmation | final_verdict")
    decision: str = Field(..., description="approve | reject")
    feedback: Optional[str] = None
    verdicts: Optional[list[dict]] = None


class PipelineListItem(BaseModel):
    id: str
    role_title: str
    status: str
    created_at: Optional[str] = None
