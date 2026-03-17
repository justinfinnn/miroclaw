"""
Anthropic LLM Client
====================
Wraps the native Anthropic Python SDK to match the LLMClient interface
(``chat()`` and ``chat_json()`` methods).

This is used when MODELING_BACKEND=openclaw and the selected provider is
``anthropic``.  It provides better compatibility than routing Anthropic
through the OpenAI SDK, since Anthropic's API has meaningful differences
(system prompt handling, message format, etc.).

Requires the ``anthropic`` package: pip install anthropic

Usage
-----
    from app.utils.anthropic_client import AnthropicLLMClient

    client = AnthropicLLMClient(api_key="sk-ant-...", model="claude-sonnet-4-20250514")
    text = client.chat([
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello!"},
    ])
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import anthropic


class AnthropicLLMClient:
    """
    LLM client wrapping the native Anthropic Python SDK.

    Provides the same ``chat()`` and ``chat_json()`` interface as LLMClient
    and CodexClient, so it can be used as a drop-in replacement.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 300.0,
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ValueError("Anthropic API key must not be empty")

        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

        self.client = anthropic.Anthropic(
            api_key=api_key,
            timeout=timeout,
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
    ) -> str:
        """
        Send a chat request to Anthropic and return the text response.

        Handles the Anthropic-specific system prompt separation (system
        messages are passed as the ``system`` parameter, not in the
        messages list).
        """
        system_prompt = None
        filtered_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # Anthropic API takes system as a top-level param
                if system_prompt is None:
                    system_prompt = content
                else:
                    system_prompt += "\n\n" + content
                continue

            # Anthropic only supports "user" and "assistant" roles
            if role not in ("user", "assistant"):
                role = "user"

            filtered_messages.append({"role": role, "content": content})

        # Ensure messages alternate user/assistant (Anthropic requirement)
        filtered_messages = self._ensure_alternating(filtered_messages)

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": filtered_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        response = self.client.messages.create(**kwargs)

        # Extract text from response content blocks
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        content = "\n".join(text_parts)
        # Strip <think> blocks from reasoning models
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """
        Like ``chat()``, but parses the response as JSON.

        Strips Markdown code fences.
        """
        raw = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Anthropic returned invalid JSON: {cleaned}") from exc

    @staticmethod
    def _ensure_alternating(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Ensure messages alternate between user and assistant roles.

        Anthropic requires strict alternation.  This merges consecutive
        same-role messages.
        """
        if not messages:
            return [{"role": "user", "content": "Hello"}]

        result = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] == result[-1]["role"]:
                # Merge with previous message of same role
                result[-1]["content"] += "\n\n" + msg["content"]
            else:
                result.append(msg)

        # Must start with user
        if result[0]["role"] != "user":
            result.insert(0, {"role": "user", "content": "(continuing conversation)"})

        return result

    def __repr__(self) -> str:
        return f"AnthropicLLMClient(model={self.model!r})"
