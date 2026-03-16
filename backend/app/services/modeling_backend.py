"""
Modeling Backend Selector
=========================
Central abstraction for choosing how MiroFish talks to the LLM.

Two modes are supported:

    api_key  (default)
        Uses a directly-configured API key (LLM_API_KEY + LLM_BASE_URL).
        Compatible with OpenAI, Alibaba DashScope, Azure OpenAI, and any
        OpenAI-SDK-compatible provider.
        This is the stable, production-ready path.

    codex    (implemented — OpenClaw Bridge + CodexClient)
        Routes LLM calls through the OpenClaw openai-codex OAuth token,
        using the correct ChatGPT backend endpoint.

        ✅ IMPLEMENTATION STATUS (verified 2026-03-14):
        ─────────────────────────────────────────────
        • OpenClaw Bridge auto-discovery: IMPLEMENTED (openclaw_bridge.py).
        • Token auto-sync on first resolve() call: IMPLEMENTED.
        • CodexClient with correct endpoint: IMPLEMENTED (codex_client.py).
          ↳ Endpoint: https://chatgpt.com/backend-api/codex/responses
          ↳ Protocol: OpenAI Responses API (SSE stream)
          ↳ Auth:     Bearer token + chatgpt-account-id (JWT-extracted)
        • Token refresh via refresh_token: IMPLEMENTED (in bridge).
        • /api/auth/codex/status endpoint: IMPLEMENTED.
        • /api/auth/codex/sync endpoint: IMPLEMENTED.

        WHY NOT api.openai.com/v1 (the old approach):
        ──────────────────────────────────────────────
        The Codex OAuth token is a ChatGPT backend session token, NOT an
        OpenAI API platform credential.  Using it against api.openai.com/v1
        always returns ``insufficient_quota`` because that endpoint requires
        paid API billing credits, not ChatGPT OAuth.  The correct target is
        chatgpt.com/backend-api/codex/responses (verified from OpenClaw
        source: @mariozechner/pi-ai/dist/providers/openai-codex-responses.js).

        ACTIVATION:
        ──────────
        1. Log in with OpenAI/Codex via OpenClaw UI (one-time setup).
        2. Set MODELING_BACKEND=codex in .env.
        3. Restart the server.

        REMAINING TODO (future pass):
        ─────────────────────────────
        [ ] Full OAuth PKCE login/redirect flow in browser (blocked on OpenAI
            publishing a public OAuth App registration for third-party apps).
        [ ] Rate-limit and quota-check middleware before dispatching calls.
        [ ] Adapt chat_json to send structured output instructions via
            system prompt (Responses API does not support response_format).

Usage
-----
    # Get a client for the currently configured backend:
    from app.services.modeling_backend import get_llm_client

    llm = get_llm_client()                   # uses MODELING_BACKEND env var
    llm = get_llm_client(credential_id="u1") # resolve codex token for user u1

    # codex mode returns a CodexClient (same .chat() / .chat_json() interface)
    # api_key mode returns an LLMClient

    # Or use the selector explicitly:
    backend = ModelingBackendSelector.get()
    print(backend.mode)          # "api_key" | "codex"
    client = backend.build_client(credential_id="u1")
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
        "api_key" or "codex".
    """

    def __init__(self, mode: str) -> None:
        if mode not in ("api_key", "codex", "ollama"):
            raise ValueError(
                f"Unknown MODELING_BACKEND {mode!r}. "
                "Valid values: 'api_key', 'codex', 'ollama'."
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
            If omitted in codex mode, falls back to the most-recently-stored
            non-expired OAuth credential, then finally to api_key mode.

        Returns
        -------
        LLMClient ready to use.
        """
        if self.mode in ("api_key", "ollama"):
            return LLMClient()  # reads LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME

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
            # Use CodexClient: the Codex OAuth token targets the ChatGPT
            # backend endpoint, NOT api.openai.com/v1.  Sending the token to
            # api.openai.com/v1 always fails with insufficient_quota because
            # it is a ChatGPT session token, not an API platform credential.
            return CodexClient(
                access_token=resolved.api_key,
                model=resolved.model or Config.CODEX_MODEL_NAME,
            )
        else:
            # No valid OAuth token found; fell back to api_key
            logger.warning(
                "[ModelingBackend] codex mode requested but no valid OAuth "
                "token found. Falling back to api_key mode via LLMClient. "
                "Store a Codex/OpenAI OAuth token via "
                "POST /api/auth/openai/credential."
            )
            return LLMClient.from_resolved(resolved)

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
