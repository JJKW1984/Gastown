"""BaseAgent — shared LiteLLM wrapper for all Gastown agents."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import litellm
from litellm import acompletion

from gastown.models import Rig
from gastown.storage import GastownDB

# Suppress LiteLLM's verbose logging unless the user opts in
litellm.set_verbose = False


class BaseAgent:
    """Base class providing a unified async LLM interface via LiteLLM.

    Subclasses set SYSTEM_PROMPT and call _call_llm().
    The model is read from GASTOWN_MODEL env var, defaulting to
    anthropic/claude-sonnet-4-6. LiteLLM auto-reads provider credentials
    from environment variables (ANTHROPIC_API_KEY, AZURE_API_KEY, etc.).
    """

    DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
    MAX_TOKENS = 8096

    def __init__(self, db: GastownDB, rig: Rig) -> None:
        self.db = db
        self.rig = rig
        self.model = os.getenv("GASTOWN_MODEL", self.DEFAULT_MODEL)

    async def _call_llm(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list[dict]] = None,
        response_format: Optional[Any] = None,
        tool_choice: Optional[Any] = None,
    ) -> Any:
        """Call the configured LLM via LiteLLM.

        Returns the full ModelResponse object. Callers access
        response.choices[0].message for content/tool_calls.
        """
        full_messages = [{"role": "system", "content": system}] + messages

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "max_tokens": self.MAX_TOKENS,
        }

        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format

        return await acompletion(**kwargs)

    def _extract_text(self, response: Any) -> str:
        """Extract plain text content from a LiteLLM response."""
        choice = response.choices[0]
        msg = choice.message
        if hasattr(msg, "content") and msg.content:
            return msg.content
        return ""

    def _extract_tool_calls(self, response: Any) -> list[dict]:
        """Extract tool_calls list from a LiteLLM response."""
        choice = response.choices[0]
        msg = choice.message
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            result = []
            for tc in msg.tool_calls:
                result.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments or "{}"),
                })
            return result
        return []

    def _stop_reason(self, response: Any) -> str:
        """Return the finish_reason string from the first choice."""
        return response.choices[0].finish_reason or "stop"
