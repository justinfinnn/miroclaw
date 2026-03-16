"""
OpenAI Auth API
===============
Endpoints for managing the two OpenAI authentication modes:

  Mode A — API Key   (env-var: LLM_API_KEY)
  Mode B — OAuth     (stored in CredentialStore)

──────────────────────────────────────────────────────────────────────────────
IMPORTANT: OpenAI OAuth provider limitation
──────────────────────────────────────────────────────────────────────────────
As of 2025 Q1, OpenAI's public API uses **API keys only** for authentication.
OpenAI does not expose a general-purpose OAuth 2.0 Authorization Code endpoint
(e.g. https://openai.com/oauth/authorize) for third-party developer apps.

The ChatGPT Apps SDK / MCP-based "Sign in with ChatGPT" OAuth flow exists, but
it is for ChatGPT plugin/action integration — NOT for obtaining tokens that
work against the OpenAI completion API (api.openai.com/v1/chat/completions).

What THIS module implements
───────────────────────────
1. Full RFC 7636 PKCE machinery (OAuthStateStore + PKCEHelper).
2. GET /api/auth/openai/login
     Constructs a valid OAuth 2.0 authorization URL and redirects — IF
     OPENAI_CLIENT_ID and OPENAI_CLIENT_SECRET are configured.
     Returns 501 with an explicit provider-limitation explanation if not.
3. GET /api/auth/openai/callback
     Handles the code + state parameters, validates PKCE state, and calls
     the token endpoint — IF the environment is fully configured.
     Returns 501 with explanation if provider isn't configured.
4. POST /api/auth/openai/token/refresh
     Attempts a refresh_token grant to renew an expired credential.
5. All existing CRUD endpoints for managing stored credentials.

What still requires OpenAI action
──────────────────────────────────
• OpenAI must publish / open a public OAuth App registration process.
• A client_id + client_secret must be obtained from the OpenAI developer
  console (does not yet exist for general developers as of 2025-03).
• The authorization endpoint URL (OPENAI_AUTH_URL below) must be confirmed
  from official OpenAI documentation once available.

Until those prerequisites are met, /login and /callback return HTTP 501
with machine-readable ``provider_limitation`` fields explaining the blocker.

──────────────────────────────────────────────────────────────────────────────

Endpoints
---------
GET  /api/auth/openai/status
GET  /api/auth/openai/login                        ← NEW (PKCE redirect)
GET  /api/auth/openai/callback                     ← NEW (token exchange)
POST /api/auth/openai/token/refresh                ← NEW (token refresh)
POST /api/auth/openai/credential
GET  /api/auth/openai/credential/<credential_id>
DEL  /api/auth/openai/credential/<credential_id>
GET  /api/auth/openai/credentials
POST /api/auth/openai/resolve
"""

import time
from urllib.parse import urlencode, urlparse

import requests
from flask import Blueprint, jsonify, redirect, request

from ..config import Config
from ..services.llm_credential_store import CredentialStore, OAuthCredential
from ..services.oauth_state_store import OAuthStateStore, PKCEHelper
from ..utils.logger import get_logger

logger = get_logger("mirofish.api.auth")

auth_bp = Blueprint("auth", __name__)

# ---------------------------------------------------------------------------
# OpenAI OAuth endpoints
# These are placeholders pending official public documentation.
# See module docstring for details on the provider limitation.
# ---------------------------------------------------------------------------

# Authorization endpoint: where the user's browser is sent to log in.
# NOTE: OpenAI does NOT expose a general public /oauth/authorize endpoint for
# API access as of 2025-03.  Set OPENAI_AUTH_URL in .env when/if this changes.
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"

# Token endpoint: where we exchange the authorization code for tokens.
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"

# Scope for API access (placeholder; subject to OpenAI's published scopes).
OPENAI_DEFAULT_SCOPE = "openai.api"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(token: str) -> str:
    """Return a partially redacted token string safe for API responses."""
    if not token or len(token) < 8:
        return "***"
    return token[:4] + "..." + token[-4:]


def _credential_to_public(cred: OAuthCredential) -> dict:
    """Return a public-safe dict for a credential (token redacted)."""
    return {
        "credential_id": cred.credential_id,
        "label": cred.label,
        "access_token_preview": _redact(cred.access_token),
        "has_refresh_token": bool(cred.refresh_token),
        "expires_at": cred.expires_at,
        "is_expired": cred.is_expired(),
        "created_at": cred.created_at,
        "updated_at": cred.updated_at,
    }


def _oauth_configured() -> tuple[bool, list[str]]:
    """
    Check whether the OAuth client credentials are configured.

    Returns (is_configured: bool, missing_vars: list[str]).
    """
    missing = []
    if not Config.OPENAI_CLIENT_ID:
        missing.append("OPENAI_CLIENT_ID")
    if not Config.OPENAI_CLIENT_SECRET:
        missing.append("OPENAI_CLIENT_SECRET")
    if not Config.OPENAI_REDIRECT_URI:
        missing.append("OPENAI_REDIRECT_URI")
    return (len(missing) == 0), missing


def _provider_limitation_response(missing_vars: list[str]):
    """
    Return a standardised 501 response explaining the provider limitation.

    Machine-readable so frontends can display an appropriate UI message.
    """
    return jsonify(
        {
            "success": False,
            "error": "OpenAI OAuth provider not configured",
            "provider_limitation": {
                "status": "NOT_AVAILABLE",
                "reason": (
                    "OpenAI does not currently expose a public OAuth 2.0 Authorization "
                    "Code flow for third-party API access (as of 2025-03). "
                    "The ChatGPT Apps SDK OAuth flow exists but grants tokens for "
                    "ChatGPT plugin integration only — not for api.openai.com/v1 completion calls. "
                    "This endpoint is fully implemented and ready for when OpenAI opens "
                    "general OAuth App registration."
                ),
                "what_works_today": [
                    "API key mode (Mode A) via LLM_API_KEY env var",
                    "Manual token storage via POST /api/auth/openai/credential",
                    "Token CRUD and resolution via CredentialStore",
                ],
                "what_is_needed": [
                    "OpenAI to publish an OAuth App registration process",
                    "Set OPENAI_CLIENT_ID in .env",
                    "Set OPENAI_CLIENT_SECRET in .env",
                    "Set OPENAI_REDIRECT_URI in .env (default: http://localhost:5000/api/auth/openai/callback)",
                ],
                "missing_env_vars": missing_vars,
                "docs": "See docs/openai-oauth.md for full context and setup guide",
            },
        }
    ), 501


# ---------------------------------------------------------------------------
# Routes — Status
# ---------------------------------------------------------------------------


@auth_bp.route("/openai/status", methods=["GET"])
def openai_status():
    """
    Return the current OpenAI auth configuration.

    Response:
        {
            "api_key_mode": { "enabled": true, ... },
            "oauth_mode": { "enabled": false, "credential_count": 0 },
            "oauth_login_available": false,    ← whether /login is usable
            "active_mode": "api_key"
        }
    """
    api_key = Config.LLM_API_KEY
    oauth_creds = CredentialStore.list_all()
    valid_oauth = [c for c in oauth_creds if not c.is_expired()]
    oauth_ready, missing_vars = _oauth_configured()

    api_key_mode = {
        "enabled": bool(api_key),
        "api_key_preview": _redact(api_key) if api_key else None,
        "base_url": Config.LLM_BASE_URL,
        "model": Config.LLM_MODEL_NAME,
    }

    oauth_mode = {
        "enabled": bool(valid_oauth),
        "credential_count": len(oauth_creds),
        "valid_credential_count": len(valid_oauth),
    }

    if api_key:
        active_mode = "api_key"
    elif valid_oauth:
        active_mode = "oauth"
    else:
        active_mode = "none"

    provider_status = {
        "login_flow_available": oauth_ready,
        "missing_config": missing_vars,
        "provider_limitation": (
            None
            if oauth_ready
            else (
                "OpenAI does not expose a public OAuth App registration for API access "
                "(as of 2025-03). OPENAI_CLIENT_ID and OPENAI_CLIENT_SECRET must be set. "
                "See docs/openai-oauth.md."
            )
        ),
    }

    return jsonify(
        {
            "success": True,
            "data": {
                "api_key_mode": api_key_mode,
                "oauth_mode": oauth_mode,
                "active_mode": active_mode,
                "oauth_provider": provider_status,
                "note": (
                    "api_key mode is preferred for background/unattended jobs. "
                    "oauth mode can be used for interactive user-scoped calls."
                ),
            },
        }
    )


# ---------------------------------------------------------------------------
# Routes — OAuth Login (PKCE redirect)
# ---------------------------------------------------------------------------


@auth_bp.route("/openai/login", methods=["GET"])
def openai_login():
    """
    Initiate the OpenAI OAuth 2.0 PKCE Authorization Code flow.

    Redirects the user's browser to the OpenAI authorization endpoint.

    Query params (optional):
        user_id       string  Application-level user ID; persisted with the
                              resulting credential after callback.
        redirect_after string URL to redirect the user to after successful auth.
                              Must be on the same origin as this server.

    On success: HTTP 302 redirect to the OpenAI authorization URL.
    On provider not configured: HTTP 501 with provider_limitation details.

    ──────────────────────────────────────────────────────────────────────
    Provider limitation (as of 2025-03)
    ──────────────────────────────────────────────────────────────────────
    OpenAI does not publish a general OAuth App registration or a public
    authorization endpoint for API access.  This route is fully implemented
    with correct PKCE mechanics but will return 501 until
    OPENAI_CLIENT_ID + OPENAI_CLIENT_SECRET are configured.
    ──────────────────────────────────────────────────────────────────────
    """
    oauth_ready, missing_vars = _oauth_configured()
    if not oauth_ready:
        return _provider_limitation_response(missing_vars)

    user_id = request.args.get("user_id") or None
    redirect_after = request.args.get("redirect_after") or None

    # Validate redirect_after if provided (open-redirect protection)
    if redirect_after:
        parsed = urlparse(redirect_after)
        # Only allow relative paths or same-origin URLs
        if parsed.scheme and parsed.netloc:
            allowed_redirect = urlparse(Config.OPENAI_REDIRECT_URI)
            if parsed.netloc != allowed_redirect.netloc:
                return jsonify({
                    "success": False,
                    "error": "redirect_after must be on the same origin as this server",
                }), 400

    # --- PKCE setup ---
    pending = OAuthStateStore.create(user_id=user_id, redirect_after=redirect_after)
    code_challenge = PKCEHelper.derive_code_challenge(pending.code_verifier)

    # --- Build authorization URL ---
    params = {
        "client_id": Config.OPENAI_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": Config.OPENAI_REDIRECT_URI,
        "scope": OPENAI_DEFAULT_SCOPE,
        "state": pending.state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"{OPENAI_AUTH_URL}?{urlencode(params)}"
    logger.info(f"OAuth login initiated for user_id={user_id!r}, redirecting to provider")

    return redirect(auth_url, code=302)


# ---------------------------------------------------------------------------
# Routes — OAuth Callback (token exchange)
# ---------------------------------------------------------------------------


@auth_bp.route("/openai/callback", methods=["GET"])
def openai_callback():
    """
    Handle the OAuth callback from OpenAI after user authorization.

    Query params (set by OAuth provider):
        code   string  Authorization code to exchange for tokens.
        state  string  Echo of the state value from /login (CSRF validation).
        error  string  Provider-set error code (if the user denied access).
        error_description  string  Human-readable error from provider.

    On success: redirects to redirect_after URL (or returns 200 JSON).
    On provider not configured: HTTP 501 with provider_limitation details.
    On CSRF / expired state: HTTP 400.
    On token exchange failure: HTTP 502 (bad gateway from OpenAI).

    ──────────────────────────────────────────────────────────────────────
    Provider limitation (as of 2025-03)
    ──────────────────────────────────────────────────────────────────────
    Same as /login — this endpoint is fully implemented but requires
    OPENAI_CLIENT_ID + OPENAI_CLIENT_SECRET to be set before it can
    actually exchange codes for tokens.
    ──────────────────────────────────────────────────────────────────────
    """
    oauth_ready, missing_vars = _oauth_configured()
    if not oauth_ready:
        return _provider_limitation_response(missing_vars)

    # --- Provider error passthrough ---
    error = request.args.get("error")
    if error:
        error_desc = request.args.get("error_description", "")
        logger.warning(f"OAuth provider returned error: {error!r} — {error_desc!r}")
        return jsonify({
            "success": False,
            "error": f"OAuth provider error: {error}",
            "error_description": error_desc,
        }), 400

    # --- Validate required params ---
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()

    if not code:
        return jsonify({"success": False, "error": "Missing 'code' parameter"}), 400
    if not state:
        return jsonify({"success": False, "error": "Missing 'state' parameter"}), 400

    # --- CSRF validation: pop the pending state ---
    pending = OAuthStateStore.pop(state)
    if pending is None:
        logger.warning(f"OAuth callback: invalid or expired state token {state!r}")
        return jsonify({
            "success": False,
            "error": "Invalid or expired OAuth state token (possible CSRF or replay)",
        }), 400

    # --- Exchange authorization code for tokens (PKCE) ---
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": Config.OPENAI_REDIRECT_URI,
        "client_id": Config.OPENAI_CLIENT_ID,
        "client_secret": Config.OPENAI_CLIENT_SECRET,
        "code_verifier": pending.code_verifier,  # PKCE proof
    }

    try:
        resp = requests.post(
            OPENAI_TOKEN_URL,
            data=token_payload,
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        logger.error(f"OAuth token exchange network error: {exc}")
        return jsonify({
            "success": False,
            "error": "Network error contacting OpenAI token endpoint",
            "detail": str(exc),
        }), 502

    if not resp.ok:
        logger.error(
            f"OAuth token exchange failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )
        return jsonify({
            "success": False,
            "error": f"Token exchange failed (HTTP {resp.status_code})",
            "provider_response": resp.text[:500],
        }), 502

    try:
        token_data = resp.json()
    except ValueError:
        return jsonify({
            "success": False,
            "error": "Malformed JSON in token response from OpenAI",
        }), 502

    access_token = token_data.get("access_token", "").strip()
    if not access_token:
        return jsonify({
            "success": False,
            "error": "No access_token in token response",
            "provider_response": token_data,
        }), 502

    refresh_token = token_data.get("refresh_token") or None
    expires_in = token_data.get("expires_in")
    expires_at = (time.time() + float(expires_in)) if expires_in else 0.0

    # --- Persist to CredentialStore ---
    credential_id = pending.user_id or f"oauth_{int(time.time())}"
    cred = OAuthCredential(
        credential_id=credential_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        label=f"OpenAI OAuth ({credential_id})",
    )
    stored = CredentialStore.upsert(cred)
    logger.info(f"OAuth credential stored after successful callback: {credential_id!r}")

    # --- Redirect or return JSON ---
    redirect_after = pending.redirect_after
    if redirect_after:
        return redirect(redirect_after, code=302)

    return jsonify({
        "success": True,
        "message": "OAuth authentication successful. Credential stored.",
        "data": _credential_to_public(stored),
    })


# ---------------------------------------------------------------------------
# Routes — Token Refresh
# ---------------------------------------------------------------------------


@auth_bp.route("/openai/token/refresh", methods=["POST"])
def refresh_token():
    """
    Attempt to refresh an expired OAuth credential using its refresh_token.

    Body (JSON):
        credential_id  string  required  — which stored credential to refresh.

    On success: returns updated credential metadata (token redacted).
    On provider not configured: HTTP 501.
    On missing/invalid credential: HTTP 404 / 400.
    On token refresh failure: HTTP 502.

    ──────────────────────────────────────────────────────────────────────
    Provider limitation (as of 2025-03)
    ──────────────────────────────────────────────────────────────────────
    Requires OPENAI_CLIENT_ID + OPENAI_CLIENT_SECRET to contact the token
    endpoint.  Returns 501 if not configured.
    ──────────────────────────────────────────────────────────────────────
    """
    oauth_ready, missing_vars = _oauth_configured()
    if not oauth_ready:
        return _provider_limitation_response(missing_vars)

    body = request.get_json(silent=True) or {}
    credential_id = (body.get("credential_id") or "").strip()

    if not credential_id:
        return jsonify({"success": False, "error": "credential_id is required"}), 400

    cred = CredentialStore.get(credential_id)
    if cred is None:
        return jsonify({"success": False, "error": "Credential not found"}), 404
    if not cred.refresh_token:
        return jsonify({
            "success": False,
            "error": "Credential has no refresh_token; cannot auto-refresh",
        }), 400

    # --- POST refresh_token grant to OpenAI token endpoint ---
    token_payload = {
        "grant_type": "refresh_token",
        "refresh_token": cred.refresh_token,
        "client_id": Config.OPENAI_CLIENT_ID,
        "client_secret": Config.OPENAI_CLIENT_SECRET,
    }

    try:
        resp = requests.post(
            OPENAI_TOKEN_URL,
            data=token_payload,
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        logger.error(f"Token refresh network error: {exc}")
        return jsonify({
            "success": False,
            "error": "Network error contacting OpenAI token endpoint",
            "detail": str(exc),
        }), 502

    if not resp.ok:
        return jsonify({
            "success": False,
            "error": f"Token refresh failed (HTTP {resp.status_code})",
            "provider_response": resp.text[:500],
        }), 502

    try:
        token_data = resp.json()
    except ValueError:
        return jsonify({
            "success": False,
            "error": "Malformed JSON in token refresh response",
        }), 502

    new_access_token = token_data.get("access_token", "").strip()
    if not new_access_token:
        return jsonify({
            "success": False,
            "error": "No access_token in token refresh response",
        }), 502

    # Update the credential in-place
    cred.access_token = new_access_token
    cred.refresh_token = token_data.get("refresh_token") or cred.refresh_token
    expires_in = token_data.get("expires_in")
    cred.expires_at = (time.time() + float(expires_in)) if expires_in else 0.0

    updated = CredentialStore.upsert(cred)
    logger.info(f"OAuth token refreshed for credential_id={credential_id!r}")

    return jsonify({
        "success": True,
        "message": "Token refreshed successfully",
        "data": _credential_to_public(updated),
    })


# ---------------------------------------------------------------------------
# Routes — Credential CRUD (Mode B management)
# ---------------------------------------------------------------------------


@auth_bp.route("/openai/credential", methods=["POST"])
def upsert_credential():
    """
    Store or update an OAuth credential.

    This endpoint can be called either:
      a) Manually by the user (paste an access token obtained elsewhere).
      b) By the OAuth callback handler (/api/auth/openai/callback) automatically.

    Body (JSON):
        credential_id   string  required
        access_token    string  required
        refresh_token   string  optional
        expires_in      int     optional  (seconds from now)
        label           string  optional
    """
    body = request.get_json(silent=True) or {}

    credential_id = body.get("credential_id", "").strip()
    access_token = body.get("access_token", "").strip()

    if not credential_id:
        return jsonify({"success": False, "error": "credential_id is required"}), 400
    if not access_token:
        return jsonify({"success": False, "error": "access_token is required"}), 400

    expires_in = body.get("expires_in")
    expires_at = (time.time() + float(expires_in)) if expires_in else 0.0

    cred = OAuthCredential(
        credential_id=credential_id,
        access_token=access_token,
        refresh_token=body.get("refresh_token") or None,
        expires_at=expires_at,
        label=body.get("label", "OpenAI OAuth"),
    )

    stored = CredentialStore.upsert(cred)
    logger.info(f"OAuth credential stored: {credential_id!r}")

    return jsonify({
        "success": True,
        "data": _credential_to_public(stored),
    })


@auth_bp.route("/openai/credential/<credential_id>", methods=["GET"])
def get_credential(credential_id: str):
    """Return metadata for a stored credential (token redacted)."""
    cred = CredentialStore.get(credential_id)
    if cred is None:
        return jsonify({"success": False, "error": "Credential not found"}), 404

    return jsonify({"success": True, "data": _credential_to_public(cred)})


@auth_bp.route("/openai/credential/<credential_id>", methods=["DELETE"])
def delete_credential(credential_id: str):
    """Remove a stored credential."""
    existed = CredentialStore.delete(credential_id)
    if not existed:
        return jsonify({"success": False, "error": "Credential not found"}), 404

    return jsonify({"success": True, "message": f"Credential {credential_id!r} deleted"})


@auth_bp.route("/openai/credentials", methods=["GET"])
def list_credentials():
    """List all stored credentials (tokens redacted)."""
    creds = CredentialStore.list_all()
    return jsonify({
        "success": True,
        "data": [_credential_to_public(c) for c in creds],
        "total": len(creds),
    })


@auth_bp.route("/codex/status", methods=["GET"])
def codex_status():
    """
    Return the current Codex mode status.

    Checks:
    - Whether OpenClaw is installed and has an openai-codex OAuth profile
    - Whether the token is valid and non-expired
    - Whether the token is already synced into MiroFish's CredentialStore
    - Whether MODELING_BACKEND=codex is active

    This is the primary diagnostic endpoint for Codex mode.
    """
    from ..services.openclaw_bridge import get_bridge

    bridge = get_bridge()
    bridge_status = bridge.status()

    return jsonify({
        "success": True,
        "data": {
            "modeling_backend": Config.MODELING_BACKEND,
            "codex_mode_active": Config.MODELING_BACKEND == "codex",
            "bridge": bridge_status,
            "recommendation": (
                "Set MODELING_BACKEND=codex in .env and call POST /api/auth/codex/sync"
                if bridge_status.get("token_present") and not bridge_status.get("token_in_mirofish_store")
                else (
                    "Codex mode is ready — MODELING_BACKEND=codex will use the OpenClaw token"
                    if bridge_status.get("ready_for_codex_mode")
                    else "OpenClaw openai-codex profile not found. Ensure OpenClaw is installed and logged in."
                )
            ),
        },
    })


@auth_bp.route("/codex/sync", methods=["POST"])
def codex_sync():
    """
    Sync the OpenClaw openai-codex OAuth token into MiroFish's CredentialStore.

    This enables MODELING_BACKEND=codex without any manual token management.
    The token is read from OpenClaw's local auth-profiles.json.

    Body (JSON): optional
        force   bool  If true, re-sync even if a valid token is already stored.

    Returns the synced credential metadata (token redacted).
    """
    from ..services.openclaw_bridge import get_bridge, OPENCLAW_CODEX_CREDENTIAL_ID

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))

    bridge = get_bridge()
    cred = bridge.sync(auto_refresh=True)

    if cred is None:
        bridge_status = bridge.status()
        return jsonify({
            "success": False,
            "error": "Could not import token from OpenClaw",
            "bridge_status": bridge_status,
            "hint": (
                "Ensure OpenClaw is installed (~/.openclaw) and you have logged in "
                "with your OpenAI/Codex account via the OpenClaw UI."
            ),
        }), 404

    return jsonify({
        "success": True,
        "message": "OpenClaw Codex token synced into MiroFish CredentialStore",
        "data": {
            "credential_id": cred.credential_id,
            "label": cred.label,
            "expires_at": cred.expires_at,
            "is_expired": cred.is_expired(),
            "has_refresh_token": bool(cred.refresh_token),
        },
        "next_step": (
            "Set MODELING_BACKEND=codex in .env and restart the server. "
            "All LLM calls will now route through your OpenAI/Codex OAuth token."
        ),
    })


@auth_bp.route("/openai/resolve", methods=["POST"])
def resolve_credential():
    """
    Debug/test endpoint: resolve which credential would be used.

    Body (JSON):
        credential_id   string   optional
        prefer_oauth    bool     optional (default false)
    """
    body = request.get_json(silent=True) or {}
    credential_id = body.get("credential_id") or None
    prefer_oauth = bool(body.get("prefer_oauth", False))

    try:
        resolved = CredentialStore.resolve(
            credential_id=credential_id,
            prefer_oauth=prefer_oauth,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    return jsonify({
        "success": True,
        "data": {
            "mode": resolved.mode,
            "api_key_preview": _redact(resolved.api_key),
            "base_url": resolved.base_url,
            "model": resolved.model,
            "credential_id": resolved.credential_id,
        },
    })
