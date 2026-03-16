"""
OAuth PKCE State Store
======================
In-memory, thread-safe store for OAuth 2.0 PKCE state tokens.

Each pending OAuth login generates a unique ``state`` value (CSRF token) and a
``code_verifier`` (PKCE secret).  Both are stored here between the /login
redirect and the /callback exchange, then deleted after use.

The store is intentionally in-memory (not persisted to disk) because:
  • State tokens are short-lived (TTL: 10 minutes).
  • Persisting them would require encrypting the code_verifier, which is
    more complex than the threat model demands for a local deployment.

A restart of the server will clear all pending OAuth flows — users will simply
need to restart their login attempt.
"""

from __future__ import annotations

import hashlib
import base64
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..utils.logger import get_logger

logger = get_logger("mirofish.oauth_state_store")

# State tokens expire after 10 minutes to limit CSRF window
_STATE_TTL_SECONDS = 10 * 60


@dataclass
class PendingOAuthState:
    """
    Everything we need to remember for an in-flight OAuth PKCE login.

    state          — opaque random string sent to the provider as ?state=...
                     and echoed back in the callback; validated for CSRF.
    code_verifier  — PKCE secret; NOT sent to provider initially; sent in
                     the token exchange to prove we initiated the flow.
    user_id        — optional application-level user identifier; stored so we
                     can tag the resulting credential.
    redirect_after — optional URL to redirect the user to after successful auth.
    created_at     — UNIX timestamp; used to enforce TTL.
    """

    state: str
    code_verifier: str
    user_id: Optional[str] = None
    redirect_after: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() > self.created_at + _STATE_TTL_SECONDS


class PKCEHelper:
    """
    Static helpers for RFC 7636 PKCE.

    PKCE (Proof Key for Code Exchange) prevents authorization code interception
    attacks in public / SPA clients.  We use S256 (SHA-256) as required by
    current best practices.

    Terminology
    -----------
    code_verifier  — cryptographically random string; kept secret; sent
                     to the token endpoint during code exchange.
    code_challenge — BASE64URL(SHA256(code_verifier)); sent to the
                     authorization endpoint during login redirect.
    code_challenge_method — always "S256" here.
    """

    @staticmethod
    def generate_code_verifier(length: int = 128) -> str:
        """
        Generate a cryptographically random code_verifier.

        RFC 7636 §4.1: between 43 and 128 characters from
        [A-Z a-z 0-9 - . _ ~].  We use 128 URL-safe base64 characters
        (≈96 bytes of entropy).
        """
        raw = os.urandom(96)
        # base64url without padding → only chars in the allowed set
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")[:length]

    @staticmethod
    def derive_code_challenge(code_verifier: str) -> str:
        """
        Compute code_challenge = BASE64URL(SHA256(ASCII(code_verifier))).

        This is the S256 method from RFC 7636 §4.2.
        """
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def generate_state() -> str:
        """Generate a cryptographically random opaque state token (CSRF)."""
        return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")


class OAuthStateStore:
    """
    Thread-safe in-memory store for pending OAuth PKCE states.

    Typical lifecycle
    -----------------
    1. /login handler calls ``OAuthStateStore.create(user_id, redirect_after)``
       → receives a PendingOAuthState with freshly-generated state + code_verifier.
    2. /login redirects the user to the provider with state + code_challenge.
    3. Provider redirects back to /callback?code=...&state=...
    4. /callback handler calls ``OAuthStateStore.pop(state)``
       → retrieves the code_verifier; state entry is deleted (one-time-use).
    5. /callback posts code + code_verifier to the token endpoint.
    """

    _store: Dict[str, PendingOAuthState] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def create(
        cls,
        user_id: Optional[str] = None,
        redirect_after: Optional[str] = None,
    ) -> PendingOAuthState:
        """
        Create and store a new PKCE state.

        Returns the PendingOAuthState (contains state + code_verifier).
        The caller should use ``state.code_challenge`` in the login redirect
        and store nothing else.
        """
        state_token = PKCEHelper.generate_state()
        code_verifier = PKCEHelper.generate_code_verifier()

        pending = PendingOAuthState(
            state=state_token,
            code_verifier=code_verifier,
            user_id=user_id,
            redirect_after=redirect_after,
        )

        with cls._lock:
            cls._purge_expired()  # housekeeping
            cls._store[state_token] = pending

        logger.debug(f"PKCE state created for user_id={user_id!r}")
        return pending

    @classmethod
    def pop(cls, state_token: str) -> Optional[PendingOAuthState]:
        """
        Retrieve and delete the pending state for ``state_token``.

        Returns None if:
          • The state token was never issued.
          • It has already been consumed (replay protection).
          • It has expired (TTL exceeded).

        This is intentionally one-time-use: calling pop() twice for the same
        state will return None on the second call.
        """
        with cls._lock:
            pending = cls._store.pop(state_token, None)

        if pending is None:
            logger.warning(f"OAuth state not found or already consumed: {state_token!r}")
            return None

        if pending.is_expired():
            logger.warning(f"OAuth state expired for state={state_token!r}")
            return None

        logger.debug(f"PKCE state consumed for user_id={pending.user_id!r}")
        return pending

    @classmethod
    def _purge_expired(cls) -> None:
        """Remove expired state entries (must be called with _lock held)."""
        expired = [k for k, v in cls._store.items() if v.is_expired()]
        for k in expired:
            del cls._store[k]
        if expired:
            logger.debug(f"Purged {len(expired)} expired OAuth state(s)")
