"""
OpenClaw Provider Registry
==========================
Maps OpenClaw provider names to their API configuration (base URL, default
models, credential extraction logic, compatibility notes).

This registry is the single source of truth for how MiroClaw constructs
LLM clients from OpenClaw auth-profiles.json entries.

Supported providers
-------------------
  ✅ Fully supported (OpenAI-compatible SDK):
     - openai          → api.openai.com/v1
     - moonshot        → api.moonshot.cn/v1
     - qwen-portal     → dashscope.aliyuncs.com/compatible-mode/v1
     - ollama          → localhost:11434/v1
     - google-gemini-cli → generativelanguage.googleapis.com/v1beta/openai/

  ✅ Fully supported (custom client):
     - openai-codex    → ChatGPT backend via CodexClient

  ⚠️ Best-effort (requires `anthropic` SDK — falls back gracefully):
     - anthropic       → api.anthropic.com (native SDK wrapper)

  ℹ️ Passthrough (unknown providers with api_key/token type):
     - Any provider with a known base_url pattern → attempted via OpenAI SDK

Usage
-----
    from app.services.openclaw_provider_registry import PROVIDER_REGISTRY, extract_credential

    info = PROVIDER_REGISTRY.get("anthropic")
    cred = extract_credential(profile_data)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProviderInfo:
    """Metadata for a known OpenClaw provider."""

    name: str
    display_name: str
    base_url: Optional[str]
    default_model: str
    compat_mode: str  # "openai" | "codex" | "anthropic" | "unknown"
    supported_models: List[str] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Provider registry — known providers and their configurations
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: Dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        name="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        compat_mode="openai",
        supported_models=["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini", "o4-mini"],
        notes="Full OpenAI API support via standard SDK.",
    ),
    "openai-codex": ProviderInfo(
        name="openai-codex",
        display_name="OpenAI Codex (ChatGPT Backend)",
        base_url=None,  # Uses ChatGPT backend, not standard API
        default_model="gpt-5.4",
        compat_mode="codex",
        supported_models=["gpt-5.4", "o4-mini"],
        notes="OAuth session token; routes through chatgpt.com/backend-api/codex/responses.",
    ),
    "anthropic": ProviderInfo(
        name="anthropic",
        display_name="Anthropic (Claude)",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-sonnet-4-20250514",
        compat_mode="anthropic",
        supported_models=[
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-3-5-haiku-20241022",
        ],
        notes="Requires anthropic SDK or OpenAI-compat header workaround.",
    ),
    "google-gemini-cli": ProviderInfo(
        name="google-gemini-cli",
        display_name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-2.5-flash",
        compat_mode="openai",
        supported_models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        notes="Google's OpenAI-compatible endpoint. Uses API key auth.",
    ),
    "moonshot": ProviderInfo(
        name="moonshot",
        display_name="Moonshot AI (Kimi)",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        compat_mode="openai",
        supported_models=["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        notes="OpenAI-compatible API.",
    ),
    "qwen-portal": ProviderInfo(
        name="qwen-portal",
        display_name="Alibaba Qwen (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        compat_mode="openai",
        supported_models=["qwen-plus", "qwen-turbo", "qwen-max", "qwen2.5-72b-instruct"],
        notes="DashScope OpenAI-compatible endpoint.",
    ),
    "ollama": ProviderInfo(
        name="ollama",
        display_name="Ollama (Local)",
        base_url="http://localhost:11434/v1",
        default_model="qwen2.5:32b",
        compat_mode="openai",
        supported_models=[],
        notes="Local Ollama instance. Model list depends on what's pulled.",
    ),
    "bedrock": ProviderInfo(
        name="bedrock",
        display_name="AWS Bedrock",
        base_url=None,
        default_model="anthropic.claude-3-sonnet-20240229-v1:0",
        compat_mode="unknown",
        supported_models=[],
        notes="AWS Bedrock requires AWS SDK. Not yet supported in openclaw mode.",
    ),
}


# ---------------------------------------------------------------------------
# Credential extraction helpers
# ---------------------------------------------------------------------------


def extract_credential(profile: dict) -> Optional[str]:
    """
    Extract the API key / token from an OpenClaw profile entry.

    Profile types and their credential fields:
      - type "api_key"  → field "key"
      - type "token"    → field "token"
      - type "oauth"    → field "access"

    Returns the credential string, or None if not found.
    """
    ptype = profile.get("type", "")

    if ptype == "api_key":
        return (profile.get("key") or "").strip() or None
    elif ptype == "token":
        return (profile.get("token") or "").strip() or None
    elif ptype == "oauth":
        return (profile.get("access") or "").strip() or None

    # Fallback: try common field names
    for field_name in ("key", "token", "access", "api_key", "apiKey"):
        val = (profile.get(field_name) or "").strip()
        if val:
            return val

    return None


def get_provider_info(provider_name: str) -> ProviderInfo:
    """
    Get provider info, falling back to a generic OpenAI-compat config
    for unknown providers.
    """
    if provider_name in PROVIDER_REGISTRY:
        return PROVIDER_REGISTRY[provider_name]

    # Unknown provider — return a best-effort generic entry
    return ProviderInfo(
        name=provider_name,
        display_name=provider_name.title(),
        base_url=None,
        default_model="",
        compat_mode="unknown",
        notes=f"Unknown provider '{provider_name}'. May work if OpenAI-compatible.",
    )
