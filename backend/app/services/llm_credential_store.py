"""
LLM Credential Store
====================
Persistent, file-backed store for per-user / per-session OAuth credentials.

This module provides the storage half of MiroClaw's dual-auth design:

    Mode A — API Key:
        Configured via environment variables (LLM_API_KEY, LLM_BASE_URL, etc.)
        Used by background/unattended jobs.

    Mode B — OAuth / user-scoped token:
        Stored here (access_token + optional refresh_token), tagged by
        credential_id (e.g. the logged-in MiroClaw user id or "default_oauth").
        Used for interactive, user-initiated LLM calls when no quota remains
        on the shared API key, or when the user has authenticated via
        OpenAI's OAuth flow.

The store intentionally does NOT implement the full OAuth redirect/callback
flow — that wiring is left for a follow-up PR.  What it *does* provide is:

  • A well-defined place to persist OAuth tokens (JSON file under uploads/).
  • CRUD helpers used by the /api/auth/* endpoints.
  • A resolve() helper that picks the right credential for a given context,
    so the rest of the codebase only calls resolve() and doesn't need to
    know which mode is active.

IMPORTANT security note
-----------------------
Tokens are stored in plaintext JSON on disk.  In a production environment
these MUST be encrypted at rest.  The current implementation is suitable
for local / trusted-server deployments only.  A TODO marker is left at the
encryption point.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("mirofish.llm_credential_store")

# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

_CREDENTIALS_DIR = os.path.join(Config.UPLOAD_FOLDER, "credentials")
_CREDENTIALS_FILE = os.path.join(_CREDENTIALS_DIR, "oauth_tokens.json")
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class OAuthCredential:
    """
    An OpenAI OAuth credential set.

    access_token  — the bearer token used in Authorization headers.
    refresh_token — optional; used to renew access_token when it expires.
    expires_at    — UNIX timestamp when access_token expires (0 = unknown).
    credential_id — unique ID for this credential, e.g. user UUID or "default_oauth".
    label         — human-readable label for the UI.
    created_at    — UNIX timestamp when this record was first stored.
    updated_at    — UNIX timestamp of last update.
    """

    credential_id: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: float = 0.0
    label: str = "OpenAI OAuth"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def is_expired(self, grace_seconds: int = 60) -> bool:
        """Return True if the access_token has expired (with a grace window)."""
        if self.expires_at == 0.0:
            return False  # unknown expiry → assume valid
        return time.time() >= (self.expires_at - grace_seconds)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # TODO: encrypt sensitive fields before persisting
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OAuthCredential":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ResolvedCredential:
    """
    The credential bundle returned by CredentialStore.resolve().

    mode        — "api_key" or "oauth"
    api_key     — the API key to pass to OpenAI SDK (may be an OAuth token)
    base_url    — LLM base URL
    model       — LLM model name
    credential_id — which credential this came from (None for env-var api_key mode)
    """

    mode: str  # "api_key" | "oauth"
    api_key: str
    base_url: str
    model: str
    credential_id: Optional[str] = None


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------


class CredentialStore:
    """
    Thread-safe, file-backed store for OAuth credentials.

    Usage
    -----
    # Persist a new OAuth credential (e.g. after OAuth callback):
    cred = OAuthCredential(
        credential_id="user_abc",
        access_token="sk-...",
        refresh_token="...",
        expires_at=time.time() + 3600,
    )
    CredentialStore.upsert(cred)

    # Resolve the right credential for a given context:
    resolved = CredentialStore.resolve(credential_id="user_abc")
    llm = LLMClient(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        model=resolved.model,
    )
    """

    # ------------------------------------------------------------------
    # Internal I/O
    # ------------------------------------------------------------------

    @classmethod
    def _load_all(cls) -> Dict[str, Dict[str, Any]]:
        """Load all stored credentials from disk (not thread-safe alone)."""
        os.makedirs(_CREDENTIALS_DIR, exist_ok=True)
        if not os.path.exists(_CREDENTIALS_FILE):
            return {}
        try:
            with open(_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Could not read credentials file: {exc}")
        return {}

    @classmethod
    def _save_all(cls, store: Dict[str, Dict[str, Any]]) -> None:
        """Persist credentials to disk (not thread-safe alone)."""
        os.makedirs(_CREDENTIALS_DIR, exist_ok=True)
        # TODO: encrypt values before writing to disk
        with open(_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Public CRUD
    # ------------------------------------------------------------------

    @classmethod
    def upsert(cls, cred: OAuthCredential) -> OAuthCredential:
        """
        Create or update an OAuth credential.

        Returns the (possibly updated) credential as stored.
        """
        with _lock:
            store = cls._load_all()
            cred.updated_at = time.time()
            if cred.credential_id not in store:
                cred.created_at = cred.updated_at
            store[cred.credential_id] = cred.to_dict()
            cls._save_all(store)
            logger.info(f"Credential upserted: {cred.credential_id!r} (mode=oauth)")
        return cred

    @classmethod
    def get(cls, credential_id: str) -> Optional[OAuthCredential]:
        """Return the credential for credential_id, or None if not found."""
        with _lock:
            store = cls._load_all()
            raw = store.get(credential_id)
        if raw is None:
            return None
        try:
            return OAuthCredential.from_dict(raw)
        except Exception as exc:
            logger.warning(f"Could not deserialize credential {credential_id!r}: {exc}")
            return None

    @classmethod
    def delete(cls, credential_id: str) -> bool:
        """Remove a credential.  Returns True if it existed."""
        with _lock:
            store = cls._load_all()
            existed = credential_id in store
            if existed:
                del store[credential_id]
                cls._save_all(store)
                logger.info(f"Credential deleted: {credential_id!r}")
        return existed

    @classmethod
    def list_all(cls) -> List[OAuthCredential]:
        """Return all stored credentials (tokens partially redacted in logs)."""
        with _lock:
            store = cls._load_all()
        result = []
        for raw in store.values():
            try:
                result.append(OAuthCredential.from_dict(raw))
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        credential_id: Optional[str] = None,
        *,
        prefer_oauth: bool = False,
    ) -> ResolvedCredential:
        """
        Select the best credential for making an LLM call.

        Resolution order
        ----------------
        1. If credential_id is given and we find a non-expired OAuth token
           for it → return that credential in "oauth" mode.
        2. If prefer_oauth=True and any non-expired OAuth credential exists
           → return the most-recently-updated one in "oauth" mode.
        3. Fall back to env-var API key mode (LLM_API_KEY / LLM_BASE_URL /
           LLM_MODEL_NAME from Config).

        Parameters
        ----------
        credential_id:
            Specific credential to look up (e.g. logged-in user's ID).
        prefer_oauth:
            If True, prefer any available OAuth credential over the API key.

        Returns
        -------
        ResolvedCredential with mode="api_key" or mode="oauth".

        Raises
        ------
        ValueError if mode=api_key and LLM_API_KEY is not configured.
        """
        # --- Attempt OAuth lookup ---
        if credential_id:
            cred = cls.get(credential_id)
            if cred and not cred.is_expired():
                logger.debug(
                    f"resolve: using oauth credential {credential_id!r}"
                )
                return ResolvedCredential(
                    mode="oauth",
                    api_key=cred.access_token,
                    # OAuth tokens talk to the real OpenAI API endpoint.
                    # Use a Codex-specific model default instead of inheriting
                    # API-key mode's model (often gpt-4o-mini).
                    base_url="https://api.openai.com/v1",
                    model=Config.CODEX_MODEL_NAME,
                    credential_id=cred.credential_id,
                )

        if prefer_oauth:
            all_creds = cls.list_all()
            valid = [c for c in all_creds if not c.is_expired()]
            if not valid:
                # No valid OAuth token in store — try to auto-sync from OpenClaw
                try:
                    from .openclaw_bridge import auto_sync_if_needed
                    synced = auto_sync_if_needed()
                    if synced:
                        all_creds = cls.list_all()
                        valid = [c for c in all_creds if not c.is_expired()]
                        logger.info(
                            "[CredentialStore] Auto-synced token from OpenClaw bridge"
                        )
                except Exception as exc:
                    logger.debug(f"[CredentialStore] OpenClaw bridge sync failed: {exc}")
            if valid:
                # Pick most recently updated
                best = max(valid, key=lambda c: c.updated_at)
                logger.debug(
                    f"resolve: prefer_oauth selected {best.credential_id!r}"
                )
                return ResolvedCredential(
                    mode="oauth",
                    api_key=best.access_token,
                    base_url="https://api.openai.com/v1",
                    model=Config.CODEX_MODEL_NAME,
                    credential_id=best.credential_id,
                )

        # --- Fall back to API key mode ---
        api_key = Config.LLM_API_KEY
        if not api_key:
            raise ValueError(
                "LLM_API_KEY is not configured and no OAuth credential is available. "
                "Set LLM_API_KEY in .env or add an OAuth credential via "
                "POST /api/auth/openai/credential."
            )
        logger.debug("resolve: using api_key mode (env var)")
        return ResolvedCredential(
            mode="api_key",
            api_key=api_key,
            base_url=Config.LLM_BASE_URL,
            model=Config.LLM_MODEL_NAME,
            credential_id=None,
        )
