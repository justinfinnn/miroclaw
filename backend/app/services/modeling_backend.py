"""
Modeling Backend Selector
=========================
Central abstraction for choosing how MiroClaw talks to the LLM.

Four modes are supported:

    ollama   (default)
        Local Ollama instance.  Fully offline.

    api_key
        Uses a directly-configured API key (LLM_API_KEY + LLM_BASE_URL).
        Compatible with OpenAI, Alibaba DashScope, Azure OpenAI, and any
        OpenAI-SDK-compatible provider.

    codex
        Routes LLM calls through the OpenClaw openai-codex OAuth token,
        using the ChatGPT backend endpoint via CodexClient.

    openclaw   ← NEW
        Reads ALL OpenClaw provider profiles from auth-profiles.json and
        constructs the appropriate LLM client for the selected provider.

        ACTIVATION:
        ──────────
        1. Ensure OpenClaw is installed and has provider profiles configured.
        2. Set MODELING_BACKEND=openclaw in .env.
        3. Optionally set OPENCLAW_PROVIDER=anthropic (or openai, etc.)
        4. Optionally set OPENCLAW_MODEL=claude-sonnet-4-6
        5. Restart the server.

        Provider selection:
        - OPENCLAW_PROVIDER env var selects which provider to use
        - If not set, picks the first available provider
        - openai-codex providers → CodexClient
        - anthropic providers → Anthropic-native client (with OpenAI SDK fallback)
        - All others → OpenAI-compatible SDK with correct base_url + api_key

Usage
-----
    from app.services.modeling_backend import get_llm_client

    llm = get_llm_client()                   # uses MODELING_BACKEND env var
    client = backend.build_client()
"""

from __future__ import annotations

import logging
from typing import Optional

from ..config import Config
from ..utils.codex_client import CodexClient
from ..utils.llm_client import LLMClient
from .llm_credential_store import CredentialStore

logger = logging.getLogger("mirofish.modeling_backend")


# ---------------------------------------------------------------------------
# ModelingBackend — immutable value object
# ---------------------------------------------------------------------------


class ModelingBackend:
    """
    Describes and constructs LLMClient instances for one backend mode.

    Attributes
    ----------
    mode : str
        "api_key", "codex", "ollama", or "openclaw".
    """

    def __init__(self, mode: str) -> None:
        if mode not in ("api_key", "codex", "ollama", "openclaw"):
            raise ValueError(
                f"Unknown MODELING_BACKEND {mode!r}. "
                "Valid values: 'api_key', 'codex', 'ollama', 'openclaw'."
            )
        self.mode = mode

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    def build_client(self, credential_id: Optional[str] = None) -> LLMClient:
        """
        Build an LLMClient for this backend mode.

        Parameters
        ----------
        credential_id:
            When mode == "codex", the OAuth credential_id to look up.
            Ignored in api_key/ollama/openclaw modes.

        Returns
        -------
        LLMClient (or CodexClient or AnthropicLLMClient) ready to use.
        """
        if self.mode in ("api_key", "ollama"):
            return LLMClient()  # reads LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME

        if self.mode == "openclaw":
            return self._build_openclaw_client()

        # --- codex mode ---
        # Resolve the best available OAuth credential.
        resolved = CredentialStore.resolve(
            credential_id=credential_id,
            prefer_oauth=True,
        )

        if resolved.mode == "oauth":
            logger.info(
                f"[ModelingBackend] codex mode — using CodexClient "
                f"(credential={resolved.credential_id!r}, "
                f"endpoint=chatgpt.com/backend-api/codex/responses)"
            )
            return CodexClient(
                access_token=resolved.api_key,
                model=resolved.model or Config.CODEX_MODEL_NAME,
            )
        else:
            logger.warning(
                "[ModelingBackend] codex mode requested but no valid OAuth "
                "token found. Falling back to api_key mode via LLMClient. "
                "Store a Codex/OpenAI OAuth token via "
                "POST /api/auth/openai/credential."
            )
            return LLMClient.from_resolved(resolved)

    # ------------------------------------------------------------------
    # OpenClaw mode — multi-provider client construction
    # ------------------------------------------------------------------

    def _build_openclaw_client(self) -> LLMClient:
        """
        Build an LLM client using OpenClaw provider profiles.

        Reads OPENCLAW_PROVIDER and OPENCLAW_MODEL from Config to determine
        which provider and model to use.  Falls back gracefully.
        """
        from .openclaw_bridge import get_bridge
        from .openclaw_provider_registry import get_provider_info

        bridge = get_bridge()
        providers = bridge.discover_providers()

        if not providers:
            logger.warning(
                "[ModelingBackend] openclaw mode: no providers found in OpenClaw. "
                "Falling back to api_key mode."
            )
            return LLMClient()

        # Select the provider
        target_provider = Config.OPENCLAW_PROVIDER
        selected = None

        if target_provider:
            # Find the requested provider
            for p in providers:
                if p["provider"] == target_provider and p["has_credential"]:
                    selected = p
                    break
            if selected is None:
                available = [p["provider"] for p in providers if p["has_credential"]]
                logger.warning(
                    f"[ModelingBackend] openclaw mode: requested provider "
                    f"{target_provider!r} not found or has no credential. "
                    f"Available: {available}. Trying first available."
                )

        if selected is None:
            # Pick the first provider with a credential
            for p in providers:
                if p["has_credential"]:
                    selected = p
                    break

        if selected is None:
            logger.error(
                "[ModelingBackend] openclaw mode: no providers have valid credentials. "
                "Falling back to api_key mode."
            )
            return LLMClient()

        provider_name = selected["provider"]
        credential = selected["credential"]
        info = get_provider_info(provider_name)
        model = Config.OPENCLAW_MODEL or info.default_model
        compat_mode = info.compat_mode

        logger.info(
            f"[ModelingBackend] openclaw mode — provider={provider_name!r}, "
            f"model={model!r}, compat_mode={compat_mode!r}"
        )

        # --- Route by compatibility mode ---

        if compat_mode == "codex":
            # openai-codex: use the existing CodexClient
            return CodexClient(
                access_token=credential,
                model=model or Config.CODEX_MODEL_NAME,
            )

        if compat_mode == "anthropic":
            return self._build_anthropic_client(credential, model)

        if compat_mode == "openai" and info.base_url:
            # Standard OpenAI-compatible provider
            return LLMClient(
                api_key=credential,
                base_url=info.base_url,
                model=model,
            )

        if info.base_url:
            # Unknown compat mode but has a base_url — try OpenAI SDK
            logger.info(
                f"[ModelingBackend] openclaw mode: unknown compat_mode "
                f"{compat_mode!r} for {provider_name!r}, attempting OpenAI SDK"
            )
            return LLMClient(
                api_key=credential,
                base_url=info.base_url,
                model=model,
            )

        # No base_url and not a special mode — can't construct a client
        logger.error(
            f"[ModelingBackend] openclaw mode: provider {provider_name!r} has "
            f"no base_url and compat_mode={compat_mode!r}. Cannot build client. "
            f"Falling back to api_key mode."
        )
        return LLMClient()

    @staticmethod
    def _build_anthropic_client(api_key: str, model: str) -> LLMClient:
        """
        Build a client for the Anthropic provider.

        Tries the native Anthropic SDK first (if installed), then falls back
        to an OpenAI-compat wrapper.
        """
        try:
            from ..utils.anthropic_client import AnthropicLLMClient
            logger.info(
                "[ModelingBackend] openclaw/anthropic: using native Anthropic SDK"
            )
            return AnthropicLLMClient(api_key=api_key, model=model)
        except ImportError:
            # anthropic SDK not installed — use OpenAI SDK against Anthropic's
            # OpenAI-compatible endpoint (limited but functional)
            logger.warning(
                "[ModelingBackend] openclaw/anthropic: 'anthropic' package not "
                "installed. Falling back to OpenAI SDK compatibility wrapper. "
                "Install 'anthropic' for full support: pip install anthropic"
            )
            return LLMClient(
                api_key=api_key,
                base_url="https://api.anthropic.com/v1",
                model=model,
            )

    def __repr__(self) -> str:
        return f"ModelingBackend(mode={self.mode!r})"


# ---------------------------------------------------------------------------
# ModelingBackendSelector — reads MODELING_BACKEND env var
# ---------------------------------------------------------------------------


class ModelingBackendSelector:
    """
    Singleton-style accessor for the globally configured ModelingBackend.

    The backend is determined by the MODELING_BACKEND environment variable
    (loaded through Config).  The instance is cached after first call.
    """

    _cached: Optional[ModelingBackend] = None

    @classmethod
    def get(cls) -> ModelingBackend:
        """Return the globally configured ModelingBackend (cached)."""
        if cls._cached is None:
            cls._cached = ModelingBackend(Config.MODELING_BACKEND)
            logger.info(
                f"[ModelingBackendSelector] active mode: {cls._cached.mode!r}"
            )
        return cls._cached

    @classmethod
    def reset(cls) -> None:
        """
        Clear the cached backend.

        Useful in tests or when MODELING_BACKEND has been changed at runtime.
        """
        cls._cached = None


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------


def get_llm_client(credential_id: Optional[str] = None) -> LLMClient:
    """
    Convenience function: return an LLMClient for the active modeling backend.

    This is the preferred entry-point for services that need an LLM client.
    It respects the MODELING_BACKEND env var transparently.

    Parameters
    ----------
    credential_id:
        When MODELING_BACKEND=codex, the OAuth credential_id to look up.
        Ignored in api_key mode.

    Examples
    --------
    # Simplest usage (api_key mode or codex depending on env):
        llm = get_llm_client()
        result = llm.chat([{"role": "user", "content": "Hello"}])

    # With a per-user credential (codex mode):
        llm = get_llm_client(credential_id=current_user_id)
        result = llm.chat_json(messages)
    """
    return ModelingBackendSelector.get().build_client(credential_id=credential_id)
