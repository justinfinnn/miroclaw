"""
CodexClient — Direct ChatGPT Codex backend HTTP client
=======================================================
Implements the correct execution path for OpenAI Codex OAuth tokens.

WHY THIS EXISTS
---------------
The OpenAI Codex OAuth token issued via OpenClaw's ``openai-codex`` OAuth flow
is **not** an OpenAI API platform credential.  It is a ChatGPT backend session
token.  Using it against ``https://api.openai.com/v1/chat/completions`` will
always return ``insufficient_quota`` because that endpoint requires paid API
platform credits — the token grants access to ChatGPT's internal Responses
backend instead.

CORRECT ENDPOINT
----------------
    https://chatgpt.com/backend-api/codex/responses

This endpoint speaks the OpenAI Responses API protocol (SSE-streamed), with a
few Codex-specific field names (e.g. ``instructions`` instead of
``system_prompt``).

REQUIRED HEADERS
----------------
    Authorization: Bearer <oauth_access_token>
    chatgpt-account-id: <account_id_from_jwt>
    OpenAI-Beta: responses=experimental
    originator: pi
    Content-Type: application/json
    Accept: text/event-stream

VERIFIED FROM
-------------
OpenClaw source: /opt/homebrew/lib/node_modules/openclaw/node_modules/@mariozechner/pi-ai/dist/providers/openai-codex-responses.js

Usage
-----
    from app.utils.codex_client import CodexClient

    client = CodexClient(access_token="eyJ...")
    text = client.chat([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Hello!"},
    ])
    print(text)
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Constants (mirroring the JS source)
# ---------------------------------------------------------------------------

CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_ENDPOINT = f"{CODEX_BASE_URL}/codex/responses"
JWT_CLAIM_PATH = "https://api.openai.com/auth"

DEFAULT_MODEL = "gpt-5.4"


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _extract_account_id(token: str) -> str:
    """
    Extract the ``chatgpt_account_id`` from the JWT access token.

    The Codex backend requires this value in the ``chatgpt-account-id`` header.
    It is stored as a custom claim at ``["https://api.openai.com/auth"]["chatgpt_account_id"]``.

    Raises ValueError if the claim is missing or the token is malformed.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Token is not a 3-part JWT")
        # Add padding if necessary
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        account_id = payload.get(JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
        if not account_id:
            raise ValueError(f"No chatgpt_account_id in JWT claim '{JWT_CLAIM_PATH}'")
        return account_id
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to extract account_id from token: {exc}") from exc


# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------

def _convert_messages_to_codex_input(messages: List[Dict[str, str]]) -> tuple[Optional[str], List[Dict]]:
    """
    Convert OpenAI chat messages into Codex Responses API format.

    The Responses API uses:
      - ``instructions`` (string) for the system prompt
      - ``input`` (list) for the conversation, where each item is
        ``{"role": "user"|"assistant", "content": [...]}``

    Returns (system_prompt_or_None, input_list)
    """
    system_prompt: Optional[str] = None
    input_messages: List[Dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_prompt = content
            continue

        # Map assistant → assistant, everything else → user
        codex_role = "assistant" if role == "assistant" else "user"

        # Codex Responses API uses different content types per role:
        #   user messages     → "input_text"
        #   assistant messages → "output_text"
        content_type = "output_text" if codex_role == "assistant" else "input_text"

        if isinstance(content, str):
            input_messages.append({
                "role": codex_role,
                "content": [{"type": content_type, "text": content}],
            })
        elif isinstance(content, list):
            # Already structured (images etc.) — pass through as-is
            input_messages.append({"role": codex_role, "content": content})
        else:
            input_messages.append({
                "role": codex_role,
                "content": [{"type": content_type, "text": str(content)}],
            })

    return system_prompt, input_messages


def _build_request_body(
    model: str,
    system_prompt: Optional[str],
    input_messages: List[Dict],
    temperature: Optional[float] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Codex Responses API request body.

    NOTE: The Codex Responses endpoint (chatgpt.com/backend-api/codex/responses)
    does NOT support the ``temperature`` parameter — sending it causes a 400
    "Unsupported parameter: temperature" error.  The parameter is accepted by
    the caller interface for compatibility with LLMClient, but is intentionally
    omitted from the request body.
    """
    body: Dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "input": input_messages,
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if system_prompt is not None:
        body["instructions"] = system_prompt
    # temperature is intentionally NOT included — the Codex Responses endpoint
    # rejects it with 400 "Unsupported parameter: temperature".
    if session_id:
        body["prompt_cache_key"] = session_id
    return body


def _build_headers(token: str, account_id: str, session_id: Optional[str] = None) -> Dict[str, str]:
    """Build required HTTP headers for the Codex endpoint."""
    headers = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": "pi",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "MiroClaw/1.0 (codex-client)",
    }
    if session_id:
        headers["session_id"] = session_id
    return headers


def _parse_sse_stream(response: requests.Response) -> str:
    """
    Parse an SSE stream from the Codex endpoint and return the assembled text.

    Handles both ``response.output_text.delta`` events (streaming text chunks)
    and ``response.completed`` events for stop detection.

    Raises RuntimeError on ``error`` or ``response.failed`` events.
    """
    text_parts: List[str] = []

    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        if etype == "error":
            code = event.get("code", "")
            msg = event.get("message", "")
            raise RuntimeError(f"Codex error: {msg or code or json.dumps(event)}")

        if etype == "response.failed":
            err_msg = event.get("response", {}).get("error", {}).get("message", "")
            raise RuntimeError(err_msg or "Codex response failed")

        # Text delta events
        if etype in ("response.output_text.delta", "text.delta"):
            delta = event.get("delta", "")
            if isinstance(delta, str):
                text_parts.append(delta)
            continue

        # text.done / output_text.done — full text for this output
        if etype in ("response.output_text.done", "text.done"):
            # Use the full accumulated text if provided; ignore individual chunk
            pass

        if etype in ("response.completed", "response.done"):
            # Stream complete — extract any final text from output if we missed deltas
            if not text_parts:
                output_list = event.get("response", {}).get("output", [])
                for out in output_list:
                    if out.get("type") == "message":
                        for content_item in out.get("content", []):
                            if content_item.get("type") in ("output_text", "text"):
                                t = content_item.get("text", "")
                                if t:
                                    text_parts.append(t)
            break

    result = "".join(text_parts).strip()
    # Strip any <think>...</think> reasoning blocks (same as existing LLMClient)
    result = re.sub(r"<think>[\s\S]*?</think>", "", result).strip()
    return result


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------

class CodexClient:
    """
    HTTP client for the ChatGPT Codex backend (``chatgpt.com/backend-api``).

    This is the correct transport for Codex OAuth tokens — the standard
    OpenAI Python SDK's ``chat.completions.create`` route (api.openai.com/v1)
    does NOT accept these tokens.

    Parameters
    ----------
    access_token:
        The OAuth access token from OpenClaw's ``openai-codex`` profile.
    model:
        Codex model name.  Defaults to ``gpt-5.4``.
    session_id:
        Optional session ID for prompt-cache keying (maps to
        ``prompt_cache_key`` in the request body).
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        access_token: str,
        model: str = DEFAULT_MODEL,
        session_id: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty")

        self.access_token = access_token
        self.model = model
        self.session_id = session_id
        self.timeout = timeout

        # Extract account_id eagerly so we fail fast on bad tokens
        self.account_id = _extract_account_id(access_token)

    # ------------------------------------------------------------------
    # Public API — matches LLMClient interface as closely as possible
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,  # accepted but not used (Responses API)
        response_format: Optional[Dict] = None,  # accepted but not used
    ) -> str:
        """
        Send a chat request to the Codex backend and return the text response.

        Parameters
        ----------
        messages:
            Standard OpenAI-format message list (``role`` + ``content``).
        temperature:
            Sampling temperature.  If None, backend default is used.
        max_tokens:
            Accepted for interface compatibility; ignored (not supported by
            the Codex Responses endpoint in the same way).
        response_format:
            Accepted for interface compatibility; ignored.

        Returns
        -------
        str — the model's response text (think-blocks stripped).

        Raises
        ------
        RuntimeError — on Codex API error events.
        requests.HTTPError — on non-2xx HTTP responses.
        ValueError — on malformed token or missing account_id.
        """
        system_prompt, input_messages = _convert_messages_to_codex_input(messages)
        body = _build_request_body(
            model=self.model,
            system_prompt=system_prompt,
            input_messages=input_messages,
            temperature=temperature,
            session_id=self.session_id,
        )
        headers = _build_headers(
            token=self.access_token,
            account_id=self.account_id,
            session_id=self.session_id,
        )

        response = requests.post(
            CODEX_ENDPOINT,
            json=body,
            headers=headers,
            stream=True,
            timeout=self.timeout,
        )
        if not response.ok:
            # Read the error body for diagnostics before raising
            try:
                err_body = response.text[:1000]
            except Exception:
                err_body = "(could not read response body)"
            raise RuntimeError(
                f"Codex endpoint returned HTTP {response.status_code}: {err_body}"
            )
        return _parse_sse_stream(response)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Like ``chat()``, but parses the response as JSON.

        Strips Markdown code fences (````json ... ````).

        Raises
        ------
        ValueError — if the response is not valid JSON.
        """
        raw = self.chat(messages=messages, temperature=temperature, max_tokens=max_tokens)

        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Codex returned invalid JSON: {cleaned}") from exc

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @classmethod
    def from_resolved(cls, resolved: Any) -> "CodexClient":
        """
        Create a CodexClient from a ResolvedCredential.

        Convenience method to match LLMClient.from_resolved().
        """
        return cls(
            access_token=resolved.api_key,
            model=resolved.model or DEFAULT_MODEL,
        )

    def __repr__(self) -> str:
        return (
            f"CodexClient("
            f"model={self.model!r}, "
            f"account_id={self.account_id[:8]!r}..., "
            f"endpoint={CODEX_ENDPOINT!r})"
        )
