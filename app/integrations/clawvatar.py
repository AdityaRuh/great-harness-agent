"""Clawvatar Engine integration — WebSocket client for avatar interviews.

Connects to VaibhavRuh-ai/clawvatar-engine running on a separate container.
Handles: avatar loading, agent speech (text → TTS → animation), mic listening.
"""

import asyncio
import base64
import json
import logging
from typing import Optional

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ClawvatarClient:
    """WebSocket client for Clawvatar Engine."""

    def __init__(self, url: str | None = None):
        self.url = (url or settings.clawvatar_url).rstrip("/")
        self.ws_url = f"{self.url}/ws"
        self.api_url = self.url.replace("ws://", "http://").replace("wss://", "https://")
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False

    async def connect(self):
        """Connect to Clawvatar WebSocket."""
        try:
            self._ws = await websockets.connect(self.ws_url, ping_interval=30)
            self._connected = True
            logger.info(f"Connected to Clawvatar at {self.ws_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Clawvatar: {e}")
            self._connected = False
            raise

    async def disconnect(self):
        """Disconnect from Clawvatar."""
        if self._ws:
            await self._ws.close()
            self._connected = False
            logger.info("Disconnected from Clawvatar")

    async def load_avatar(self, model_path: str) -> dict:
        """Load a VRM/GLB avatar on the server."""
        if not self._ws:
            raise RuntimeError("Not connected to Clawvatar")

        await self._ws.send(json.dumps({
            "type": "avatar.load",
            "model_path": model_path,
        }))

        response = json.loads(await self._ws.recv())
        if response.get("type") == "avatar.ready":
            logger.info(f"Avatar loaded: {response.get('info', {}).get('name', 'unknown')}")
            return response.get("info", {})
        elif response.get("type") == "error":
            raise RuntimeError(f"Avatar load failed: {response.get('message')}")
        return response

    async def send_agent_speech(self, text: str, system_prompt: str = "") -> dict:
        """Send text for the avatar to speak.

        Uses Clawvatar's /api/chat endpoint which handles:
        text → LLM response (or direct speech) → Piper TTS → animation frames

        For interview mode, we bypass the LLM and send the text directly
        to the TTS+animation pipeline via WebSocket batch mode.
        """
        if not self._ws:
            raise RuntimeError("Not connected to Clawvatar")

        # For direct speech (interviewer already has the text to say),
        # we use the agent pipeline approach: synthesize TTS on server,
        # then send the audio as batch for animation
        import httpx

        try:
            # Use the REST endpoint for full pipeline
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.api_url}/api/chat",
                    json={
                        "text": text,
                        "system_prompt": system_prompt or "Speak this text directly.",
                        "api_key": "direct_speech",  # Server-side bypass for direct TTS
                    },
                )
                result = resp.json()

                if "error" in result:
                    logger.warning(f"Clawvatar speech error: {result['error']}")
                    return {"text": text, "error": result["error"]}

                logger.info(
                    f"Avatar speech: '{text[:50]}...' → "
                    f"{result.get('frame_count', 0)} frames, "
                    f"{result.get('duration', 0):.1f}s, "
                    f"{result.get('compute_ms', 0):.0f}ms"
                )
                return result

        except httpx.ConnectError:
            logger.warning("Clawvatar REST API not available, using WebSocket fallback")
            return await self._ws_speak_fallback(text)
        except Exception as e:
            logger.error(f"Speech failed: {e}")
            return {"text": text, "error": str(e)}

    async def _ws_speak_fallback(self, text: str) -> dict:
        """Fallback: send pre-synthesized audio via WebSocket batch mode."""
        # Without the REST API, we can't do TTS — return text-only result
        return {
            "text": text,
            "frames": [],
            "duration": 0,
            "fallback": True,
            "message": "Clawvatar TTS unavailable — text-only mode",
        }

    async def send_audio_batch(self, audio_bytes: bytes, sample_rate: int = 16000) -> dict:
        """Send audio batch and receive animation frames."""
        if not self._ws:
            raise RuntimeError("Not connected to Clawvatar")

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self._ws.send(json.dumps({
            "type": "audio.batch",
            "data": audio_b64,
            "sample_rate": sample_rate,
            "chunk_size": 1024,
        }))

        response = json.loads(await self._ws.recv())
        return response

    async def listen_for_speech(
        self,
        timeout_seconds: int = 120,
        silence_threshold_seconds: float = 3.0,
    ) -> list[bytes]:
        """Listen for candidate's microphone input via WebSocket streaming.

        The Clawvatar frontend captures mic audio and sends it to the server.
        We collect audio chunks until silence is detected or timeout.

        Returns list of audio chunk bytes.
        """
        if not self._ws:
            raise RuntimeError("Not connected to Clawvatar")

        audio_chunks = []
        silence_start = None

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    raw = await self._ws.recv()
                    msg = json.loads(raw)

                    if msg.get("type") == "candidate_audio":
                        # Candidate is speaking — collect audio
                        audio_b64 = msg.get("data", "")
                        if audio_b64:
                            audio_chunks.append(base64.b64decode(audio_b64))
                            silence_start = None

                    elif msg.get("type") == "candidate_silence":
                        # Silence detected
                        if silence_start is None:
                            silence_start = asyncio.get_event_loop().time()
                        elif (asyncio.get_event_loop().time() - silence_start) >= silence_threshold_seconds:
                            logger.info(f"Silence threshold reached ({silence_threshold_seconds}s)")
                            break

                    elif msg.get("type") == "candidate_done":
                        # Candidate explicitly finished (clicked "done" button)
                        break

        except TimeoutError:
            logger.info(f"Listen timeout after {timeout_seconds}s")

        logger.info(f"Collected {len(audio_chunks)} audio chunks from candidate")
        return audio_chunks

    async def health_check(self) -> dict:
        """Check if Clawvatar server is healthy."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.api_url}/health")
                return resp.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None
