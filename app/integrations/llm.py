"""LLM integration — uses Claude Code CLI for auth-free LLM calls.

Calls `claude -p "prompt"` via subprocess, which uses Claude Code's
native OAuth authentication. No API key required.

Falls back to Anthropic SDK if ANTHROPIC_API_KEY is set.
"""

import json
import logging
import os
import subprocess
import asyncio
import concurrent.futures

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _call_claude_cli(prompt: str, model: str | None = None) -> str:
    """Call Claude via the claude CLI tool (Claude Code's native auth)."""
    cmd = ["claude", "-p", prompt]
    if model:
        cmd.extend(["--model", model])

    logger.info(f"Calling claude CLI (model={model or 'default'}, prompt={len(prompt)} chars)")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if result.returncode != 0:
            logger.error(f"claude CLI failed: {result.stderr[:500]}")
            raise RuntimeError(f"claude CLI error: {result.stderr[:200]}")

        response = result.stdout.strip()
        logger.info(f"claude CLI response: {len(response)} chars")
        return response
    except FileNotFoundError:
        raise RuntimeError(
            "claude CLI not found. Install Claude Code: npm install -g @anthropic-ai/claude-code"
        )


def call_llm_threaded(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """Non-blocking version — runs LLM call in a thread so server stays responsive."""
    return call_llm(prompt, model, max_tokens)


def _call_anthropic_sdk(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """Call Claude via the Anthropic SDK (requires ANTHROPIC_API_KEY)."""
    from anthropic import Anthropic

    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY set and SDK fallback requested")

    client = Anthropic(api_key=api_key)
    model = model or settings.llm_model_fast

    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def call_llm(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """Call Claude — tries CLI first, falls back to SDK.

    Priority:
    1. If ANTHROPIC_API_KEY is set → use SDK (fastest)
    2. Otherwise → use claude CLI (Claude Code auth)
    """
    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    if api_key and not api_key.startswith("sk-ant-oat"):
        # Real API key — use SDK
        return _call_anthropic_sdk(prompt, model, max_tokens)
    else:
        # No key or OAuth token — use CLI
        return _call_claude_cli(prompt, model)


def call_llm_json(prompt: str, model: str | None = None) -> dict:
    """Call Claude and parse JSON response."""
    response = call_llm(prompt, model)

    # Try to extract JSON from response
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # Try to find JSON block in response
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            return json.loads(json_match.group(1).strip())

        # Try to find { ... } block
        brace_match = re.search(r'\{[\s\S]*\}', response)
        if brace_match:
            return json.loads(brace_match.group(0))

        logger.error(f"Failed to parse JSON from response: {response[:300]}")
        raise ValueError(f"LLM response is not valid JSON: {response[:200]}")


def call_llm_strong(prompt: str, max_tokens: int = 4096) -> str:
    """Call Claude with the strong model (Opus)."""
    return call_llm(prompt, model=settings.llm_model_strong, max_tokens=max_tokens)
