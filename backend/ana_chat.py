import os
import re
import time
import uuid
import logging
from typing import Optional
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

COMPASS_SYSTEM_PROMPT = (
    "You are COMPASS, the AI travel companion inside YATRMIND TRAVEL AI AGENT. You help with this trip: "
    "answer questions about the itinerary, budget, or destination, and give general "
    "travel advice. You can also answer unrelated questions. Be warm, direct, and "
    "concise -- a few sentences unless more detail is genuinely needed. Never invent a "
    "specific place name, price, or rating you're not sure is real -- speak in general "
    "terms instead (\"a rooftop bar in that area\" rather than naming one). You cannot "
    "edit the itinerary yourself -- if the user wants a change, say what you'd suggest "
    "and that they can regenerate the trip with that in mind."
)
ANA_SYSTEM_PROMPT = COMPASS_SYSTEM_PROMPT

MAX_SESSIONS = 300
MAX_HISTORY_PER_SESSION = 12


class ChatSession:
    def __init__(self, thread_id: str, itinerary_context: Optional[str] = None):
        self.thread_id = thread_id
        self.itinerary_context = itinerary_context or ""
        self.messages: list[dict[str, str]] = []
        self.updated_at = time.time()

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})
        self._trim_history()
        self.updated_at = time.time()

    def add_assistant_message(self, content: str):
        self.messages.append({"role": "assistant", "content": content})
        self._trim_history()
        self.updated_at = time.time()

    def _trim_history(self):
        if len(self.messages) > MAX_HISTORY_PER_SESSION:
            # Keep the most recent messages
            self.messages = self.messages[-MAX_HISTORY_PER_SESSION:]


class CompassCompanion:
    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}

    def _get_client(self) -> Groq:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is missing from environment.")
        return Groq(api_key=api_key)

    def _get_or_create_session(self, thread_id: Optional[str], itinerary_context: Optional[str] = None) -> ChatSession:
        if not thread_id:
            thread_id = f"compass_{uuid.uuid4().hex[:12]}"

        # Housekeeping: prune old sessions if exceeding MAX_SESSIONS
        if len(self._sessions) > MAX_SESSIONS:
            oldest = sorted(self._sessions.items(), key=lambda item: item[1].updated_at)
            for k, _ in oldest[: len(self._sessions) - MAX_SESSIONS + 10]:
                self._sessions.pop(k, None)

        if thread_id not in self._sessions:
            self._sessions[thread_id] = ChatSession(thread_id, itinerary_context)
        else:
            if itinerary_context and not self._sessions[thread_id].itinerary_context:
                self._sessions[thread_id].itinerary_context = itinerary_context

        return self._sessions[thread_id]

    def ask(self, user_message: str, thread_id: Optional[str] = None, itinerary_context: Optional[str] = None) -> tuple[str, str]:
        """
        Sends a user message to COMPASS with conversational memory and returns (answer, thread_id).
        """
        clean_msg = (user_message or "").strip()
        if not clean_msg:
            return "How can I help you with your trip today?", thread_id or f"compass_{uuid.uuid4().hex[:12]}"

        session = self._get_or_create_session(thread_id, itinerary_context)
        session.add_user_message(clean_msg)

        system_instruction = COMPASS_SYSTEM_PROMPT
        if session.itinerary_context:
            system_instruction += (
                "\n\n=== CURRENT TRIP ITINERARY CONTEXT ===\n"
                f"{session.itinerary_context}\n"
                "====================================="
            )

        messages = [{"role": "system", "content": system_instruction}]
        messages.extend(session.messages)

        client = self._get_client()
        model_name = os.getenv("GROQ_CHAT_MODEL", os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"))
        max_tokens = int(os.getenv("GROQ_CHAT_MAX_TOKENS", "400"))

        answer = ""
        for attempt in range(2):
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.6,
                )
                answer = (response.choices[0].message.content or "").strip()
                break
            except Exception as exc:
                exc_str = str(exc)
                is_rate_limit = "429" in exc_str or "rate_limit" in exc_str.lower()
                if is_rate_limit and attempt == 0:
                    wait_s = 3.0
                    match = re.search(r"try again in\s+([\d.]+)s", exc_str, re.IGNORECASE)
                    if match:
                        try:
                            wait_s = min(float(match.group(1)) + 0.2, 8.0)
                        except ValueError:
                            pass
                    logger.warning(f"COMPASS: Groq rate limit hit, backing off {wait_s}s...")
                    time.sleep(wait_s)
                    continue

                logger.error(f"COMPASS: Chat completion failed ({type(exc).__name__}: {exc})")
                answer = "I'm having a little trouble connecting right now. Please ask me again in just a moment!"
                break

        if not answer:
            answer = "I'm here to help with your trip! What would you like to know?"

        session.add_assistant_message(answer)
        return answer, session.thread_id


AnaCompanion = CompassCompanion
# Global instances
compass_companion = CompassCompanion()
ana_companion = compass_companion
