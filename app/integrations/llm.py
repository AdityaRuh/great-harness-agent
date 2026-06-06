"""OpenAI Codex (ChatGPT OAuth) — sole LLM provider.

Calls ChatGPT's backend Responses endpoint with an OAuth bearer token
issued to the `codex` CLI. Access tokens are refreshed automatically
when they expire using the long-lived refresh token.

Public API (unchanged from prior Claude integration):
    call_llm / acall_llm
    call_llm_json / acall_llm_json
    call_llm_strong / acall_llm_strong
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from threading import Lock

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REFRESH_SCOPE = "openid profile email offline_access"
_REFRESH_SKEW_SECONDS = 60


@dataclass
class _Tokens:
    access_token: str
    refresh_token: str
    expires_at_ms: int

    def is_expiring(self) -> bool:
        return time.time() * 1000 >= (self.expires_at_ms - _REFRESH_SKEW_SECONDS * 1000)


_tokens_lock = Lock()
_tokens: _Tokens | None = None


def _bootstrap_tokens() -> _Tokens:
    if not settings.openai_codex_access_token or not settings.openai_codex_refresh_token:
        raise RuntimeError(
            "OpenAI Codex OAuth not configured. Set OPENAI_CODEX_ACCESS_TOKEN and "
            "OPENAI_CODEX_REFRESH_TOKEN in .env."
        )
    return _Tokens(
        access_token=settings.openai_codex_access_token,
        refresh_token=settings.openai_codex_refresh_token,
        expires_at_ms=settings.openai_codex_expires_ms or 0,
    )


def _refresh_payload(refresh_token: str) -> dict:
    return {
        "client_id": settings.openai_codex_client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": _REFRESH_SCOPE,
    }


def _tokens_from_refresh_response(prev: _Tokens, data: dict) -> _Tokens:
    return _Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", prev.refresh_token),
        expires_at_ms=int(time.time() * 1000) + int(data.get("expires_in", 28800)) * 1000,
    )


def _refresh_sync(current: _Tokens) -> _Tokens:
    logger.info("Refreshing OpenAI Codex access token (sync)")
    r = httpx.post(_OAUTH_TOKEN_URL, json=_refresh_payload(current.refresh_token), timeout=30)
    if r.status_code >= 400:
        logger.error(f"Codex token refresh failed: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
    return _tokens_from_refresh_response(current, r.json())


async def _refresh_async(current: _Tokens) -> _Tokens:
    logger.info("Refreshing OpenAI Codex access token (async)")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(_OAUTH_TOKEN_URL, json=_refresh_payload(current.refresh_token))
        if r.status_code >= 400:
            logger.error(f"Codex token refresh failed: {r.status_code} {r.text[:300]}")
            r.raise_for_status()
        return _tokens_from_refresh_response(current, r.json())


def _get_tokens_sync() -> _Tokens:
    global _tokens
    with _tokens_lock:
        if _tokens is None:
            _tokens = _bootstrap_tokens()
        if _tokens.is_expiring():
            _tokens = _refresh_sync(_tokens)
        return _tokens


async def _get_tokens_async() -> _Tokens:
    global _tokens
    with _tokens_lock:
        if _tokens is None:
            _tokens = _bootstrap_tokens()
        current = _tokens
    if current.is_expiring():
        refreshed = await _refresh_async(current)
        with _tokens_lock:
            _tokens = refreshed
        return refreshed
    return current


def _set_tokens(new: _Tokens) -> None:
    global _tokens
    with _tokens_lock:
        _tokens = new


def _build_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "chatgpt-account-id": settings.openai_codex_account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "session_id": str(uuid.uuid4()),
    }


_DEFAULT_INSTRUCTIONS = (
    "You are a precise, helpful assistant powering an AI hiring pipeline. "
    "Follow the user's instructions exactly. When the user asks for JSON, return "
    "ONLY valid JSON with no prose, no markdown fences, and no commentary."
)


def _build_payload(prompt: str, model: str | None) -> dict:
    return {
        "model": model or settings.openai_codex_model,
        "instructions": settings.openai_codex_instructions or _DEFAULT_INSTRUCTIONS,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "stream": True,
        "store": False,
        "reasoning": {"effort": settings.openai_codex_thinking or "medium", "summary": "auto"},
    }


def _parse_sse_event(raw: str) -> dict | None:
    if not raw or not raw.startswith("data:"):
        return None
    data = raw[len("data:"):].strip()
    if not data or data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _accumulate_event(ev: dict, chunks: list[str]) -> None:
    ev_type = ev.get("type")
    if ev_type == "response.output_text.delta":
        delta = ev.get("delta")
        if isinstance(delta, str):
            chunks.append(delta)
    elif ev_type == "response.failed":
        err = ev.get("response", {}).get("error") or ev.get("error") or {}
        raise RuntimeError(f"Codex Responses failed: {err}")
    elif ev_type == "response.completed":
        # If the server only sent a single response.output_text instead of deltas,
        # pull the final text out of the completed envelope.
        if not chunks:
            for item in ev.get("response", {}).get("output", []) or []:
                for part in item.get("content", []) or []:
                    text = part.get("text")
                    if isinstance(text, str):
                        chunks.append(text)


def _consume_sse_sync(stream: httpx.Response) -> str:
    chunks: list[str] = []
    for raw in stream.iter_lines():
        ev = _parse_sse_event(raw)
        if ev is not None:
            _accumulate_event(ev, chunks)
    return "".join(chunks).strip()


async def _consume_sse_async(stream: httpx.Response) -> str:
    chunks: list[str] = []
    async for raw in stream.aiter_lines():
        ev = _parse_sse_event(raw)
        if ev is not None:
            _accumulate_event(ev, chunks)
    return "".join(chunks).strip()


def _post_responses_sync(prompt: str, model: str | None) -> str:
    tokens = _get_tokens_sync()
    payload = _build_payload(prompt, model)
    logger.info(f"Codex Responses SYNC (model={payload['model']}, prompt={len(prompt)} chars)")
    timeout = httpx.Timeout(240.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        for attempt in range(2):
            headers = _build_headers(tokens.access_token)
            with client.stream("POST", _CHATGPT_RESPONSES_URL, headers=headers, json=payload) as r:
                if r.status_code == 401 and attempt == 0:
                    logger.info("401 from Codex; refreshing token and retrying")
                    tokens = _refresh_sync(tokens)
                    _set_tokens(tokens)
                    continue
                if r.status_code >= 400:
                    body = b"".join(r.iter_raw()).decode(errors="replace")[:500]
                    logger.error(f"Codex Responses error {r.status_code}: {body}")
                    raise RuntimeError(f"Codex Responses error {r.status_code}: {body[:200]}")
                text = _consume_sse_sync(r)
                logger.info(f"Codex Responses returned {len(text)} chars")
                return text
    raise RuntimeError("Codex Responses: exhausted retries")


async def _post_responses_async(prompt: str, model: str | None) -> str:
    tokens = await _get_tokens_async()
    payload = _build_payload(prompt, model)
    logger.info(f"Codex Responses ASYNC (model={payload['model']}, prompt={len(prompt)} chars)")
    timeout = httpx.Timeout(240.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            headers = _build_headers(tokens.access_token)
            async with client.stream("POST", _CHATGPT_RESPONSES_URL, headers=headers, json=payload) as r:
                if r.status_code == 401 and attempt == 0:
                    logger.info("401 from Codex; refreshing token and retrying")
                    tokens = await _refresh_async(tokens)
                    _set_tokens(tokens)
                    continue
                if r.status_code >= 400:
                    body = (b"".join([c async for c in r.aiter_raw()])).decode(errors="replace")[:500]
                    logger.error(f"Codex Responses error {r.status_code}: {body}")
                    raise RuntimeError(f"Codex Responses error {r.status_code}: {body[:200]}")
                text = await _consume_sse_async(r)
                logger.info(f"Codex Responses returned {len(text)} chars")
                return text
    raise RuntimeError("Codex Responses: exhausted retries")


def call_llm(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """SYNC call — blocks event loop. Use acall_llm() for non-blocking."""
    return _post_responses_sync(prompt, model)


async def acall_llm(prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
    """ASYNC non-blocking LLM call — server stays responsive."""
    return await _post_responses_async(prompt, model)


def call_llm_json(prompt: str, model: str | None = None) -> dict:
    return _parse_json(call_llm(prompt, model))


async def acall_llm_json(prompt: str, model: str | None = None) -> dict:
    return _parse_json(await acall_llm(prompt, model))


def call_llm_strong(prompt: str, max_tokens: int = 4096) -> str:
    return call_llm(prompt, model=settings.openai_codex_model_strong or settings.openai_codex_model, max_tokens=max_tokens)


async def acall_llm_strong(prompt: str, max_tokens: int = 4096) -> str:
    return await acall_llm(prompt, model=settings.openai_codex_model_strong or settings.openai_codex_model, max_tokens=max_tokens)


def _parse_json(response: str) -> dict:
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if json_match:
            return json.loads(json_match.group(1).strip())
        brace_match = re.search(r"\{[\s\S]*\}", response)
        if brace_match:
            return json.loads(brace_match.group(0))
        logger.error(f"Failed to parse JSON: {response[:300]}")
        raise ValueError(f"Not valid JSON: {response[:200]}")
