"""Knowledgebase reader — reads ruh-org-knowledgebase catalog and docs."""

import glob
import logging
import os

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def read_index() -> str:
    """Read the master INDEX.md."""
    path = os.path.join(settings.knowledgebase_path, "INDEX.md")
    if os.path.isfile(path):
        return open(path).read()
    logger.warning(f"INDEX.md not found at {path}")
    return ""


def list_catalog_repos() -> list[str]:
    """List all repo names in the catalog."""
    catalog_dir = os.path.join(settings.knowledgebase_path, "catalog")
    if not os.path.isdir(catalog_dir):
        return []
    return [
        os.path.basename(f).replace(".md", "")
        for f in sorted(glob.glob(f"{catalog_dir}/*.md"))
    ]


def read_catalog_entry(repo_name: str) -> str:
    """Read a specific repo's catalog entry."""
    path = os.path.join(settings.knowledgebase_path, "catalog", f"{repo_name}.md")
    if os.path.isfile(path):
        return open(path).read()
    return ""


def read_catalog_entries(repo_names: list[str]) -> dict[str, str]:
    """Read multiple catalog entries."""
    return {name: read_catalog_entry(name) for name in repo_names if read_catalog_entry(name)}


def search_knowledge_base(keyword: str) -> list[dict]:
    """Search across all knowledge base docs for a keyword."""
    results = []
    kb_path = settings.knowledgebase_path

    for md_file in glob.glob(f"{kb_path}/**/*.md", recursive=True):
        try:
            content = open(md_file).read()
            if keyword.lower() in content.lower():
                rel_path = os.path.relpath(md_file, kb_path)
                # Find matching lines
                matches = [
                    line.strip()
                    for line in content.split("\n")
                    if keyword.lower() in line.lower()
                ][:3]
                results.append({"file": rel_path, "matches": matches})
        except Exception:
            continue

    return results


def get_frontend_repos() -> dict[str, str]:
    """Get all frontend repo catalog entries."""
    frontend_repos = [
        "ruh-fe", "ruh-app-fe", "ruh-auth-fe", "marketplace-frontend",
        "workflow-builder-app", "ruh-super-admin-fe", "ruh-design-system",
        "ruh-support-fe", "ruh-admin-fe", "ruh-dev-fe", "ruh-main-fe",
    ]
    return read_catalog_entries(frontend_repos)


def get_backend_repos() -> dict[str, str]:
    """Get all backend repo catalog entries."""
    backend_repos = [
        "agent-gateway", "agent-service-backend", "ruh-ai-api-gateway",
        "communication-service", "user-service", "notification-service",
        "ruh-scheduler-service", "sdr-backend", "sdr-worker",
    ]
    return read_catalog_entries(backend_repos)


def get_repos_by_role(role_title: str) -> dict[str, str]:
    """Intelligently select repos relevant to a role title."""
    role_lower = role_title.lower()

    if any(kw in role_lower for kw in ["frontend", "front-end", "ui", "react", "next"]):
        return get_frontend_repos()
    elif any(kw in role_lower for kw in ["backend", "back-end", "api", "python", "fastapi"]):
        return get_backend_repos()
    elif any(kw in role_lower for kw in ["fullstack", "full-stack", "full stack"]):
        return {**get_frontend_repos(), **get_backend_repos()}
    elif any(kw in role_lower for kw in ["devops", "infra", "sre", "platform"]):
        repos = ["agent-gateway", "ruh-scheduler-service", "development-conductor",
                 "file-conversion", "inbox-rotation-service"]
        return read_catalog_entries(repos)
    elif any(kw in role_lower for kw in ["data", "ml", "ai", "machine learning"]):
        repos = ["langgraph", "agent-service-backend", "ruh-ai-api-gateway"]
        return read_catalog_entries(repos)
    else:
        # Return everything for unknown roles
        all_repos = list_catalog_repos()
        return read_catalog_entries(all_repos[:15])  # Top 15 to avoid context overflow
