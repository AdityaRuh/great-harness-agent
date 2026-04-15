"""Avatar interview session — orchestrates a live AI avatar interview.

Flow per candidate:
1. Connect to Clawvatar WebSocket
2. Load VRM avatar (company interviewer)
3. Avatar greets candidate
4. Loop through questions:
   a. Avatar asks question (text → Piper TTS → lip-sync → Three.js)
   b. Candidate responds via microphone
   c. Audio transcribed (faster-whisper STT)
   d. Claude scores the response
   e. Avatar reacts (nod/tilt/followup)
5. Avatar closing remarks
6. Save transcript + scores
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.integrations.clawvatar import ClawvatarClient
from app.integrations.llm import call_llm, call_llm_json
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AvatarInterviewSession:
    """Manages a single candidate's avatar interview."""

    def __init__(
        self,
        session_id: str,
        candidate: dict,
        questions: list[dict],
        role_title: str,
        clawvatar_url: str | None = None,
    ):
        self.session_id = session_id
        self.candidate = candidate
        self.questions = questions
        self.role_title = role_title
        self.clawvatar = ClawvatarClient(clawvatar_url)

        self.transcript: list[dict] = []
        self.scores: list[dict] = []
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.status = "pending"

    async def run(self) -> dict:
        """Run the full avatar interview. Returns transcript + scores."""

        self.started_at = datetime.now(timezone.utc)
        self.status = "in_progress"
        name = self.candidate.get("name", "there")

        logger.info(f"Starting avatar interview: {name} for {self.role_title}")

        try:
            # Connect to Clawvatar
            await self.clawvatar.connect()

            # Load interviewer avatar
            try:
                await self.clawvatar.load_avatar("avatars/interviewer.vrm")
            except Exception as e:
                logger.warning(f"Avatar load failed (non-fatal): {e}")

            # Greeting
            greeting = (
                f"Hi {name}! Welcome to your interview for the {self.role_title} position. "
                f"I'll be asking you about {len(self.questions)} questions. "
                f"Take your time with each answer, and feel free to ask me to repeat anything. "
                f"Ready to get started?"
            )
            await self._avatar_speak(greeting)
            ready_response = await self._listen_for_response(max_seconds=30)
            self.transcript.append({
                "type": "greeting",
                "interviewer": greeting,
                "candidate": ready_response,
            })

            # Question loop
            for i, question in enumerate(self.questions):
                logger.info(f"  Question {i+1}/{len(self.questions)}: {question.get('category', '?')}")

                # Avatar asks the question
                await self._avatar_speak(question["text"])

                # Listen for candidate response
                response = await self._listen_for_response(
                    max_seconds=question.get("max_time_seconds", 120)
                )

                # Score the response
                score = self._evaluate_response(question, response)

                # Record transcript
                entry = {
                    "question_index": i,
                    "category": question.get("category", "unknown"),
                    "skill_tested": question.get("skill_being_tested", ""),
                    "question": question["text"],
                    "response": response,
                    "score": score,
                }

                # Handle follow-up for vague answers
                if score.get("quality") == "vague" and question.get("follow_up"):
                    followup_text = question["follow_up"]
                    await self._avatar_speak(f"Could you elaborate a bit more? {followup_text}")
                    followup_response = await self._listen_for_response(max_seconds=60)
                    followup_score = self._evaluate_response(question, followup_response)
                    entry["followup_question"] = followup_text
                    entry["followup_response"] = followup_response
                    entry["followup_score"] = followup_score
                    # Use better of original and followup
                    if followup_score.get("score", 0) > score.get("score", 0):
                        entry["score"] = followup_score

                self.transcript.append(entry)
                self.scores.append(entry["score"])

                # Natural transition to next question
                if i < len(self.questions) - 1:
                    quality = score.get("quality", "adequate")
                    if quality in ("great", "good"):
                        transition = "Great, thank you. "
                    elif quality == "vague":
                        transition = "I appreciate you sharing that. "
                    else:
                        transition = "Thank you for your answer. "

                    if i == len(self.questions) - 2:
                        transition += "Last question coming up."
                    else:
                        transition += "Let's move on."

                    await self._avatar_speak(transition)

            # Closing
            closing = (
                "That wraps up our interview! Thank you so much for your time. "
                "We'll review your responses and get back to you soon. "
                "Do you have any questions for us?"
            )
            await self._avatar_speak(closing)
            final_response = await self._listen_for_response(max_seconds=120)
            self.transcript.append({
                "type": "closing",
                "interviewer": closing,
                "candidate_questions": final_response,
            })

            await self.clawvatar.disconnect()

        except Exception as e:
            logger.error(f"Interview session error: {e}", exc_info=True)
            self.status = "error"
            return self._build_result(error=str(e))

        self.completed_at = datetime.now(timezone.utc)
        self.status = "completed"

        return self._build_result()

    async def _avatar_speak(self, text: str):
        """Make the avatar speak with lip-sync and expressions."""
        try:
            await self.clawvatar.send_agent_speech(text)
        except Exception as e:
            logger.warning(f"Avatar speech failed (non-fatal): {e}")
            # Interview continues in text-only mode

    async def _listen_for_response(self, max_seconds: int = 120) -> str:
        """Listen for candidate's spoken response, transcribe to text."""
        try:
            audio_chunks = await self.clawvatar.listen_for_speech(
                timeout_seconds=max_seconds,
                silence_threshold_seconds=3.0,
            )
            if audio_chunks:
                return _transcribe_audio(audio_chunks)
            return "[No response detected]"
        except Exception as e:
            logger.warning(f"Listen failed: {e}")
            return "[Audio capture failed]"

    def _evaluate_response(self, question: dict, response: str) -> dict:
        """Claude Sonnet scores the response against the rubric."""
        if not response or response.startswith("["):
            return {"score": 0, "quality": "no_response", "notes": "No response captured"}

        prompt = f"""Score this interview response.

Question: {question['text']}
Skill being tested: {question.get('skill_being_tested', 'general')}
Rubric:
  Great (9-10): {question.get('rubric', {}).get('great', 'Excellent, detailed answer')}
  Good (6-8): {question.get('rubric', {}).get('good', 'Adequate answer')}
  Poor (1-5): {question.get('rubric', {}).get('poor', 'Weak or missing answer')}

Candidate's response: "{response}"

Return JSON:
{{
    "score": 7,
    "quality": "great | good | adequate | vague | poor | no_response",
    "strengths": ["..."],
    "weaknesses": ["..."],
    "notes": "1-2 sentence assessment"
}}"""

        try:
            return call_llm_json(prompt)
        except Exception as e:
            return {"score": 5, "quality": "adequate", "notes": f"Scoring error: {e}"}

    def _build_result(self, error: str = None) -> dict:
        """Build the final interview result."""
        valid_scores = [s for s in self.scores if s.get("score", 0) > 0]
        avg_score = (sum(s["score"] for s in valid_scores) / len(valid_scores) * 10) if valid_scores else 0

        duration = None
        if self.started_at and self.completed_at:
            duration = round((self.completed_at - self.started_at).total_seconds() / 60, 1)

        return {
            "session_id": self.session_id,
            "candidate_name": self.candidate.get("name", "unknown"),
            "role_title": self.role_title,
            "status": self.status,
            "transcript": self.transcript,
            "scores": self.scores,
            "total_score": round(avg_score, 1),
            "questions_asked": len(self.scores),
            "questions_answered": len([s for s in self.scores if s.get("quality") != "no_response"]),
            "duration_minutes": duration,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": error,
        }


def _transcribe_audio(audio_chunks: list[bytes]) -> str:
    """Transcribe audio chunks to text using faster-whisper (local, free).

    Falls back to a placeholder if whisper is not installed.
    """
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        combined = b"".join(audio_chunks)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            # Write raw PCM as WAV
            import struct
            sr = 16000
            n_samples = len(combined) // 2
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + len(combined)))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16))
            f.write(b"data")
            f.write(struct.pack("<I", len(combined)))
            f.write(combined)
            tmp_path = f.name

        segments, _ = model.transcribe(tmp_path)
        text = " ".join(seg.text for seg in segments).strip()
        os.unlink(tmp_path)
        return text if text else "[Silence]"

    except ImportError:
        logger.warning("faster-whisper not installed — using placeholder transcription")
        return "[Audio transcription requires faster-whisper — install with: pip install faster-whisper]"
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return f"[Transcription error: {e}]"
