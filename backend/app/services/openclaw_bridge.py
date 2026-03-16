"""
OpenClaw Bridge
===============
Reads the local OpenClaw credential store and imports the ``openai-codex``
OAuth access token into MiroFish's CredentialStore.

This enables MODELING_BACKEND=codex to work automatically on a machine where
OpenClaw is installed and the user has logged in via the OpenClaw UI.

How it works
------------
1. Locate the OpenClaw auth-profiles.json for the running agent (or discover
   via OPENCLAW_AGENT env var or a default search path).
2. Read the ``openai-codex:default`` profile's ``access`` token and ``expires``
   timestamp.
3. Optionally: refresh the token via the ``refresh`` token stored there,
   using OpenAI's token refresh endpoint.
4. Upsert the token into MiroFish's CredentialStore under the canonical
   credential_id ``"openclaw_codex"``.

The bridge is called lazily from CredentialStore.resolve() when
MODELING_BACKEND=codex and no other OAuth token is available.

CREDENTIAL_ID used
------------------
``openclaw_codex``  — identifies tokens that originated from OpenClaw's
                      openai-codex OAuth profile.

Security note
-------------
The auth-profiles.json file contains an access_token in plaintext.  This
bridge reads it with the same trust level as any other local-file secret.
The file is owned by the OpenClaw process and lives in the user's home
directory under ~/.openclaw/agents/<agent_name>/agent/.

Usage
-----
    from app.services.openclaw_bridge import OpenClawBridge

    # Import from OpenClaw profile if available:
    bridge = OpenClawBridge()
    cred = bridge.sync()            # returns OAuthCredential or None
    if cred:
        print("synced", cred.credential_id, "expires_at", cred.expires_at)

    # Check status:
    status = bridge.status()        # returns dict with diagnostics
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger("mirofish.openclaw_bridge")

# The credential_id under which OpenClaw-sourced tokens are stored
OPENCLAW_CODEX_CREDENTIAL_ID = "openclaw_codex"

# OpenAI token refresh endpoint
OPENAI_TOKEN_REFRESH_URL = "https://auth.openai.com/oauth/token"

# OpenAI's OAuth client_id used by OpenClaw (public value, extracted from JWT)
# This is the OpenClaw app client_id registered with OpenAI
OPENCLAW_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


class OpenClawBridge:
    """
    Bridges OpenClaw's local OAuth credential store to MiroFish.

    Searches for auth-profiles.json in standard OpenClaw locations and
    imports the ``openai-codex:default`` token.
    """

    def __init__(self) -> None:
        self._profiles_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _find_profiles_file(self) -> Optional[Path]:
        """
        Locate the OpenClaw auth-profiles.json that contains the
        ``openai-codex:default`` profile.

        Search order:
        1. OPENCLAW_AGENT_PROFILES env var (explicit override).
        2. Standard per-agent path: ~/.openclaw/agents/<OPENCLAW_AGENT>/agent/auth-profiles.json
           where OPENCLAW_AGENT is read from the environment.
        3. Walk all ~/.openclaw/agents/*/agent/auth-profiles.json and pick the
           first one that has an openai-codex profile.
        4. ~/.openclaw/agents/backend-architect/agent/auth-profiles.json
           (the agent that typically runs MiroFish tasks in this repo).
        """
        # --- 1. Explicit override ---
        explicit = os.environ.get("OPENCLAW_AGENT_PROFILES")
        if explicit and Path(explicit).exists():
            return Path(explicit)

        openclaw_dir = Path.home() / ".openclaw"
        agents_dir = openclaw_dir / "agents"

        # --- 2. Env-specified agent ---
        agent_name = os.environ.get("OPENCLAW_AGENT")
        if agent_name:
            candidate = agents_dir / agent_name / "agent" / "auth-profiles.json"
            if candidate.exists():
                return candidate

        # --- 4. Known default: backend-architect (used for MiroFish work) ---
        known = agents_dir / "backend-architect" / "agent" / "auth-profiles.json"
        if known.exists() and self._has_codex_profile(known):
            return known

        # --- 3. Walk all agents ---
        if agents_dir.is_dir():
            for candidate in sorted(agents_dir.glob("*/agent/auth-profiles.json")):
                if self._has_codex_profile(candidate):
                    return candidate

        return None

    @staticmethod
    def _has_codex_profile(path: Path) -> bool:
        """Return True if the profiles file has an openai-codex entry."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profiles = data.get("profiles", {})
            return any("openai-codex" in k for k in profiles)
        except Exception:
            return False

    def _load_profiles(self) -> Optional[dict]:
        """Load and return the raw profiles JSON, or None on failure."""
        if self._profiles_path is None:
            self._profiles_path = self._find_profiles_file()
        if self._profiles_path is None:
            logger.debug("[OpenClawBridge] No auth-profiles.json found")
            return None
        try:
            data = json.loads(self._profiles_path.read_text(encoding="utf-8"))
            return data
        except Exception as exc:
            logger.warning(f"[OpenClawBridge] Could not read {self._profiles_path}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Token extraction
    # ------------------------------------------------------------------

    def _get_codex_profile(self) -> Optional[dict]:
        """Return the raw openai-codex profile dict, or None."""
        data = self._load_profiles()
        if data is None:
            return None
        profiles = data.get("profiles", {})
        # Prefer "openai-codex:default"; fall back to any openai-codex profile
        profile = profiles.get("openai-codex:default") or next(
            (v for k, v in profiles.items() if "openai-codex" in k), None
        )
        return profile

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def _try_refresh(self, refresh_token: str) -> Optional[dict]:
        """
        Attempt to refresh the access token using the refresh_token.

        Returns the token response dict on success, None on failure.
        This is a best-effort operation; failures are logged and ignored.
        """
        try:
            import requests as req_lib
            resp = req_lib.post(
                OPENAI_TOKEN_REFRESH_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": OPENCLAW_CLIENT_ID,
                },
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if resp.ok:
                token_data = resp.json()
                if token_data.get("access_token"):
                    logger.info("[OpenClawBridge] Token refresh succeeded")
                    return token_data
            logger.debug(
                f"[OpenClawBridge] Token refresh failed: HTTP {resp.status_code}"
            )
        except Exception as exc:
            logger.debug(f"[OpenClawBridge] Token refresh error: {exc}")
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self, *, auto_refresh: bool = True) -> "Optional[OAuthCredential]":
        """
        Import the OpenClaw openai-codex token into MiroFish's CredentialStore.

        Parameters
        ----------
        auto_refresh:
            If True (default), attempt to refresh an expired token using the
            stored refresh_token before giving up.

        Returns
        -------
        OAuthCredential if a token was successfully imported, else None.
        """
        # Import here to avoid circular imports
        from .llm_credential_store import CredentialStore, OAuthCredential

        profile = self._get_codex_profile()
        if profile is None:
            logger.debug("[OpenClawBridge] No openai-codex profile found; skipping sync")
            return None

        access_token = profile.get("access", "").strip()
        refresh_token = profile.get("refresh", "").strip() or None
        expires_ms = profile.get("expires", 0)
        expires_at = expires_ms / 1000 if expires_ms else 0.0

        if not access_token:
            logger.debug("[OpenClawBridge] openai-codex profile has no access token")
            return None

        # Check if token is expired and we have a refresh_token
        is_expired = expires_at > 0 and time.time() >= (expires_at - 60)
        if is_expired and refresh_token and auto_refresh:
            logger.info("[OpenClawBridge] Token appears expired; attempting refresh")
            refreshed = self._try_refresh(refresh_token)
            if refreshed:
                access_token = refreshed.get("access_token", access_token)
                new_expires_in = refreshed.get("expires_in")
                if new_expires_in:
                    expires_at = time.time() + float(new_expires_in)
                refresh_token = refreshed.get("refresh_token", refresh_token)

        account_id = profile.get("accountId", "")
        label = f"OpenClaw openai-codex (account: {account_id[:8]}...)" if account_id else "OpenClaw openai-codex"

        cred = OAuthCredential(
            credential_id=OPENCLAW_CODEX_CREDENTIAL_ID,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            label=label,
        )
        stored = CredentialStore.upsert(cred)
        logger.info(
            f"[OpenClawBridge] Synced token → CredentialStore "
            f"(credential_id={OPENCLAW_CODEX_CREDENTIAL_ID!r}, "
            f"expires_at={expires_at:.0f}, "
            f"profiles_path={self._profiles_path})"
        )
        return stored

    def status(self) -> dict:
        """
        Return a diagnostic status dict for the OpenClaw bridge.

        Includes:
        - whether a profiles file was found
        - whether an openai-codex profile exists
        - token validity (without exposing the token itself)
        - whether the token is in MiroFish's CredentialStore
        """
        from .llm_credential_store import CredentialStore

        profiles_path = self._find_profiles_file()
        profile = self._get_codex_profile() if profiles_path else None

        has_profile = profile is not None
        token_present = bool(profile.get("access") if profile else False)
        expires_ms = profile.get("expires", 0) if profile else 0
        expires_at = expires_ms / 1000 if expires_ms else 0.0
        token_valid = (expires_at == 0 or time.time() < expires_at - 60) if token_present else False
        has_refresh = bool(profile.get("refresh") if profile else False)

        # Check MiroFish CredentialStore
        stored_cred = CredentialStore.get(OPENCLAW_CODEX_CREDENTIAL_ID)
        in_store = stored_cred is not None
        store_valid = stored_cred is not None and not stored_cred.is_expired()

        return {
            "openclaw_profiles_found": profiles_path is not None,
            "profiles_path": str(profiles_path) if profiles_path else None,
            "codex_profile_present": has_profile,
            "token_present": token_present,
            "token_valid": token_valid,
            "token_has_refresh": has_refresh,
            "expires_at": expires_at,
            "token_in_mirofish_store": in_store,
            "store_token_valid": store_valid,
            "credential_id": OPENCLAW_CODEX_CREDENTIAL_ID,
            "ready_for_codex_mode": token_present and token_valid,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bridge_instance: Optional[OpenClawBridge] = None


def get_bridge() -> OpenClawBridge:
    """Return the module-level OpenClawBridge singleton."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = OpenClawBridge()
    return _bridge_instance


def auto_sync_if_needed(*, force: bool = False) -> bool:
    """
    Sync the OpenClaw Codex token into MiroFish's CredentialStore if needed.

    Called lazily by CredentialStore.resolve() when MODELING_BACKEND=codex
    and no token is available.

    Parameters
    ----------
    force:
        If True, sync even if a valid token is already in the store.

    Returns
    -------
    True if a token is now available in the store, False otherwise.
    """
    from .llm_credential_store import CredentialStore

    if not force:
        existing = CredentialStore.get(OPENCLAW_CODEX_CREDENTIAL_ID)
        if existing and not existing.is_expired():
            return True  # Already have a valid token

    bridge = get_bridge()
    cred = bridge.sync()
    return cred is not None and not cred.is_expired()
