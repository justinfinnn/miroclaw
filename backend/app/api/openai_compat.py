"""
OpenAI-compatible adapter endpoints.

These endpoints let internal components that expect the OpenAI Chat
Completions shape talk to MiroClaw's configured modeling backend
(``ollama``, ``api_key``, ``codex``, or ``openclaw``) through one stable
HTTP interface.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import jsonify, request

from . import openai_compat_bp
from ..config import Config
from ..services.modeling_backend import get_llm_client
from ..utils.logger import get_logger

logger = get_logger("mirofish.api.openai_compat")


def _normalize_message_content(content: Any) -> str:
    """Collapse OpenAI-style content blocks into plain text."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)

    return str(content or "")


def _normalize_messages(payload: dict) -> list[dict[str, str]]:
    messages = payload.get("messages") or []
    normalized = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        normalized.append({
            "role": message.get("role", "user"),
            "content": _normalize_message_content(message.get("content", "")),
        })

    return normalized


def _completion_response(content: str, model: str) -> dict:
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


@openai_compat_bp.route("/v1/models", methods=["GET"])
def list_models():
    """Expose the active model so OpenAI-compatible clients can introspect it."""
    model_name = (
        Config.OPENCLAW_MODEL
        or Config.CODEX_MODEL_NAME
        or Config.LLM_MODEL_NAME
        or "miroclaw-auto"
    )

    return jsonify({
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": 0,
                "owned_by": "miroclaw",
            }
        ],
    })


@openai_compat_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """OpenAI Chat Completions-compatible adapter."""
    payload = request.get_json(silent=True) or {}
    messages = _normalize_messages(payload)

    if not messages:
        return jsonify({
            "error": {
                "message": "messages must be a non-empty array",
                "type": "invalid_request_error",
            }
        }), 400

    if payload.get("stream"):
        return jsonify({
            "error": {
                "message": "stream=true is not supported by the local adapter",
                "type": "invalid_request_error",
            }
        }), 400

    temperature = payload.get("temperature", 0.7)
    max_tokens = payload.get("max_tokens", 4096)
    response_format = payload.get("response_format")
    requested_model = payload.get("model")

    try:
        client = get_llm_client()
        if requested_model and hasattr(client, "model"):
            client.model = requested_model

        content = client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        model_name = getattr(client, "model", requested_model or Config.LLM_MODEL_NAME)
        return jsonify(_completion_response(content, model_name))
    except Exception as exc:
        logger.error("OpenAI-compatible adapter request failed: %s", exc)
        return jsonify({
            "error": {
                "message": str(exc),
                "type": "server_error",
            }
        }), 500
