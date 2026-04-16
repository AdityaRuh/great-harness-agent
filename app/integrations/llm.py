"""LLM integration — uses Claude Code CLI for auth-free LLM calls.

Uses asyncio.create_subprocess_exec for NON-BLOCKING CLI calls,
so the server can handle other requests while Claude processes.
"""

import asyncio
import json
import logging
import os
import subprocess

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _call_claude_cli(prompt: str, model: str | None = None) -> str:
    """SYNC — Call Claude via the claude CLI tool. BLOCKS the event loop."""
    cmd = ["claude", "-p", prompt]
    if model:
        cmd.extend(["--model", model])

    logger.info(f"Calling claude CLI SYNC (model={model or 'default'}, prompt={len(prompt)} chars)")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if result.returncode != 0:
            logger.error(f"claude CLI failed: {result.stderr[:500]}")
            raise RuntimeError(f"claude CLI error: {result.stderr[:200]}")
        response = result.stdout.strip()
        logger.info(f"claude CLI response: {len(response)} chars")
        return response
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found. Install Claude Code.")


async def _async_call_claude_cli(prompt: str, model: str | None = None) -> str:
    """ASYNC — Call Claude via CLI WITHOUT blocking the event loop."""
    cmd = ["claude", "-p", prompt]
    if model:
        cmd.extend(["--model", model])

    logger.info(f"Calling claude CLI ASYNC (model={model or 'default'}, prompt={len(prompt)} chars)")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=240)

        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else "unknown error"
            logger.error(f"claude CLI failed: {err[:500]}")
            raise RuntimeError(f"claude CLI error: {err[:200]}")

        response = stdout.decode().strip() if stdout else ""
        logger.info(f"claude CLI response: {len(response)} chars")
        return response
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found. Install Claude Code.")
    except asyncio.TimeoutError:
        logger.error("claude CLI timed out after 240s")
        raise RuntimeError("claude CLI timed out")


def _call_anthropic_sdk(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """Call Claude via the Anthropic SDK (requires ANTHROPIC_API_KEY)."""
    from anthropic import Anthropic
    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY set")
    client = Anthropic(api_key=api_key)
    model = model or settings.llm_model_fast
    msg = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _has_api_key() -> bool:
    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    return bool(api_key and not api_key.startswith("sk-ant-oat"))


def call_llm(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """SYNC call — blocks event loop. Use acall_llm() for non-blocking."""
    if _has_api_key():
        return _call_anthropic_sdk(prompt, model, max_tokens)
    return _call_claude_cli(prompt, model)


async def acall_llm(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """ASYNC non-blocking LLM call — server stays responsive."""
    if _has_api_key():
        return await asyncio.to_thread(_call_anthropic_sdk, prompt, model, max_tokens)
    return await _async_call_claude_cli(prompt, model)


def call_llm_json(prompt: str, model: str | None = None) -> dict:
    """SYNC JSON call."""
    return _parse_json(call_llm(prompt, model))


async def acall_llm_json(prompt: str, model: str | None = None) -> dict:
    """ASYNC non-blocking JSON call."""
    return _parse_json(await acall_llm(prompt, model))


def call_llm_strong(prompt: str, max_tokens: int = 4096) -> str:
    """SYNC strong model call."""
    return call_llm(prompt, model=settings.llm_model_strong, max_tokens=max_tokens)


async def acall_llm_strong(prompt: str, max_tokens: int = 4096) -> str:
    """ASYNC strong model call."""
    return await acall_llm(prompt, model=settings.llm_model_strong, max_tokens=max_tokens)


def _parse_json(response: str) -> dict:
    """Parse JSON from LLM response."""
    import re
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            return json.loads(json_match.group(1).strip())
        brace_match = re.search(r'\{[\s\S]*\}', response)
        if brace_match:
            return json.loads(brace_match.group(0))
        logger.error(f"Failed to parse JSON: {response[:300]}")
        raise ValueError(f"Not valid JSON: {response[:200]}")
