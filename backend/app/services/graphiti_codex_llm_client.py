"""
GraphitiCodexLLMClient
======================
A Graphiti-compatible LLMClient that routes all LLM calls through
the OpenClaw Codex (ChatGPT OAuth) endpoint instead of api.openai.com/v1.

WHY THIS EXISTS
---------------
Graphiti's default OpenAIClient uses api.openai.com/v1 which requires
a paid API platform credential.  In Codex mode, MiroClaw uses an OpenAI
OAuth access token from OpenClaw that is only valid for the ChatGPT
backend endpoint (chatgpt.com/backend-api/codex/responses).

This client bridges Graphiti's abstract LLMClient interface to MiroClaw's
CodexClient, enabling fully Codex-backed graph builds.

RATE LIMITING STRATEGY
-----------------------
The Codex endpoint enforces aggressive rate limits.  This client applies:

1. Per-call exponential back-off with jitter (up to 10 retries, 5 min max wait).
2. A global asyncio semaphore (CODEX_GRAPHITI_CONCURRENCY, default 1) to
   serialise concurrent Graphiti internal LLM calls so we never fire more
   than N calls simultaneously — avoiding burst rate-limit storms.
3. A minimum inter-call delay (CODEX_GRAPHITI_MIN_DELAY_SECS, default 2.0 s)
   to pace requests even when the semaphore allows N > 1.

USAGE
-----
Constructed automatically by GraphitiGraphProvider._get_client() when
MODELING_BACKEND=codex.  Not intended for direct use.

AGPL-3.0 — see repository root LICENSE
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from typing import Any, Optional

from pydantic import BaseModel

from ..utils.logger import get_logger

logger = get_logger("mirofish.graphiti_codex_llm_client")


# ---------------------------------------------------------------------------
# NoOpCrossEncoder — stub for Codex mode (cross-encoder only used in search)
# ---------------------------------------------------------------------------
# NOTE: graphiti_core is an optional dependency. This module is only used
# when MODELING_BACKEND=codex AND graphiti_core is installed.

try:
    from graphiti_core.cross_encoder.client import CrossEncoderClient as _CEClient
    from graphiti_core.embedder.client import EmbedderClient as _EmbedderClient
    _GRAPHITI_AVAILABLE = True
except ImportError:
    _GRAPHITI_AVAILABLE = False
    _CEClient = object
    _EmbedderClient = object


class LocalHashEmbedder(_EmbedderClient):
    """
    Deterministic hash-based embedder for Codex mode.

    Produces 1024-dimensional float vectors from text using a seeded hash.
    Vectors are L2-normalised so cosine similarity is well-defined.

    WHY: The OpenAI embeddings endpoint requires a paid API key, which is
    not available in pure Codex mode.  This embedder lets graph *build*
    succeed end-to-end.  Semantic similarity quality is degraded (hash
    collisions, no semantic relationship between vectors) but:
      • Entities ARE extracted and stored in Neo4j.
      • Edges ARE extracted and stored.
      • Deduplication uses LLM-based comparison, not just vector similarity,
        so the graph is still semantically meaningful.
      • The embedder can be swapped for a real one later without rebuilding.

    Dimension matches OpenAI text-embedding-3-small (1024) so Neo4j
    vector indexes remain compatible if the embedder is later upgraded.
    """

    EMBEDDING_DIM = 1024

    def _embed(self, text: str) -> list[float]:
        """Produce a deterministic 1024-d unit vector from *text*."""
        import hashlib
        import math

        # Generate deterministic bytes from the text
        seed_bytes = hashlib.sha512(text.encode("utf-8", errors="replace")).digest()

        # Expand to EMBEDDING_DIM floats using repeated hashing
        floats: list[float] = []
        chunk = seed_bytes
        while len(floats) < self.EMBEDDING_DIM:
            chunk = hashlib.sha256(chunk).digest()
            for i in range(0, len(chunk) - 3, 4):
                # Interpret 4 bytes as a signed int32, normalise to [-1, 1]
                val = int.from_bytes(chunk[i:i+4], "big", signed=True)
                floats.append(val / 2_147_483_648.0)

        vec = floats[:self.EMBEDDING_DIM]

        # L2 normalise
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    async def create(self, input_data) -> list[float]:
        if isinstance(input_data, str):
            return self._embed(input_data)
        elif isinstance(input_data, list) and input_data:
            # list[str] — embed first item (Graphiti passes single-item lists)
            return self._embed(str(input_data[0]))
        return self._embed("")

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in input_data_list]


class NoOpCrossEncoder(_CEClient):
    """
    Stub CrossEncoderClient for Graphiti in Codex mode.

    The cross-encoder is used only during *search/retrieval* operations to
    re-rank candidate passages.  Graph *build* (add_episode) does not call
    the cross-encoder at all.

    In Codex mode we avoid initialising the real OpenAIRerankerClient (which
    requires an API key) and use this no-op stub instead.  Search results
    will lose reranking quality but graph construction is fully functional.
    """

    async def rank(self, query: str, passages: list) -> list:
        """Return passages in original order with uniform scores."""
        return [(p, 0.5) for p in passages]

# ---------------------------------------------------------------------------
# Tuning constants (override via env vars)
# ---------------------------------------------------------------------------

# Maximum concurrent Graphiti → Codex calls.
# 1 = fully serialised (safest for rate limits).
# Raise to 2-3 only if your Codex quota allows burst.
_CONCURRENCY = int(os.environ.get("CODEX_GRAPHITI_CONCURRENCY", "1"))

# Minimum seconds between consecutive Codex calls (additional pacing layer).
_MIN_DELAY = float(os.environ.get("CODEX_GRAPHITI_MIN_DELAY_SECS", "2.0"))

# Retry policy for rate-limit errors
_MAX_RETRIES = int(os.environ.get("CODEX_GRAPHITI_MAX_RETRIES", "10"))
_INITIAL_BACKOFF = float(os.environ.get("CODEX_GRAPHITI_INITIAL_BACKOFF", "10.0"))
_MAX_BACKOFF = float(os.environ.get("CODEX_GRAPHITI_MAX_BACKOFF", "300.0"))  # 5 min
_BACKOFF_FACTOR = float(os.environ.get("CODEX_GRAPHITI_BACKOFF_FACTOR", "2.0"))

# ---------------------------------------------------------------------------
# Lazy imports to avoid circular-import issues at module load time
# ---------------------------------------------------------------------------


def _get_codex_client():
    """Return a configured CodexClient using the current OpenClaw Codex token."""
    from ..services.openclaw_bridge import auto_sync_if_needed
    from ..services.llm_credential_store import CredentialStore
    from ..utils.codex_client import CodexClient
    from ..config import Config

    # Ensure the OpenClaw bridge has synced the token
    auto_sync_if_needed()

    cred = CredentialStore.resolve(prefer_oauth=True)
    if cred.mode != "oauth":
        raise RuntimeError(
            "GraphitiCodexLLMClient: No valid Codex OAuth token available. "
            "Log in with OpenClaw's openai-codex OAuth flow and retry."
        )

    return CodexClient(
        access_token=cred.api_key,
        model=Config.CODEX_MODEL_NAME or "gpt-5.4",
    )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


try:
    from graphiti_core.llm_client.client import LLMClient as _GraphitiLLMClient
    from graphiti_core.llm_client.config import LLMConfig as _LLMConfig
except ImportError:
    _GraphitiLLMClient = object
    _LLMConfig = None


class GraphitiCodexLLMClient(_GraphitiLLMClient):  # type: ignore[misc]
    """
    Graphiti-compatible LLM client backed by the Codex SSE endpoint.
    
    NOTE: Requires graphiti_core to be installed. If not available,
    this class inherits from object and will raise ImportError on use.

    Subclasses Graphiti's LLMClient ABC and implements _generate_response()
    to route calls through MiroClaw's CodexClient (ChatGPT OAuth backend).

    The base class generate_response() / _generate_response_with_retry()
    machinery is bypassed: we override generate_response() directly to
    apply our own rate-limit pacing and retry logic instead of relying on
    tenacity (which doesn't understand Codex-specific rate-limit signals).
    """

    # Module-level semaphore shared across all instances in a process.
    # Created lazily on the *dedicated* event loop (via _DedicatedLoop) so
    # it is always associated with the one loop that runs all Graphiti async
    # calls — avoiding "Future attached to a different loop" errors.
    _semaphore: Optional[asyncio.Semaphore] = None
    _semaphore_lock = threading.Lock()
    _last_call_ts: float = 0.0

    # ---------------------------------------------------------------------------
    # Constructor
    # ---------------------------------------------------------------------------

    def __init__(self) -> None:
        from ..config import Config

        # Initialize the LLMClient base (sets up tracer, token_tracker, etc.)
        config = _LLMConfig(
            model=Config.CODEX_MODEL_NAME or "gpt-5.4",
        )
        super().__init__(config=config, cache=False)

        # Override model attrs used by Graphiti
        self.model = Config.CODEX_MODEL_NAME or "gpt-5.4"
        self.small_model = self.model
        self.temperature = 0.0
        self.max_tokens = 16384

        # Lazy client — obtained fresh on first _call() to pick up latest token
        self._codex_client = None

    # ---------------------------------------------------------------------------
    # Semaphore (created on the dedicated event loop, thread-safe init)
    # ---------------------------------------------------------------------------

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        if cls._semaphore is None:
            with cls._semaphore_lock:
                if cls._semaphore is None:
                    # Create the semaphore directly — asyncio.Semaphore() does
                    # NOT need to be awaited or created inside a coroutine.
                    # The old code used run_coroutine_threadsafe() which
                    # deadlocked when called from a coroutine already running
                    # on the dedicated loop (the loop can't run a new coroutine
                    # while it's blocked waiting for .result()).
                    cls._semaphore = asyncio.Semaphore(_CONCURRENCY)
        return cls._semaphore

    # ---------------------------------------------------------------------------
    # Token
    # ---------------------------------------------------------------------------

    def _client(self):
        if self._codex_client is None:
            self._codex_client = _get_codex_client()
        return self._codex_client

    # ---------------------------------------------------------------------------
    # Core call with rate-limit retry
    # ---------------------------------------------------------------------------

    def _call_codex_sync(self, messages_dicts: list[dict], response_model=None) -> dict:
        """
        Synchronous call to Codex with exponential back-off on rate-limit errors.

        Parameters
        ----------
        messages_dicts : list[dict]
            Standard OpenAI-format message dicts (role + content).
        response_model : pydantic.BaseModel subclass | None
            If provided, schema is appended to the last user message so that
            the model returns structured JSON matching that schema.

        Returns
        -------
        dict parsed from the model's JSON response.
        """
        # Append schema instruction when a response model is provided
        if response_model is not None:
            schema = json.dumps(response_model.model_json_schema(), indent=2)
            instruction = (
                f"\n\nRespond with a JSON object that strictly conforms to this schema:\n\n"
                f"{schema}\n\nOutput only the JSON object, no markdown or extra text."
            )
            # Append to last user message
            msgs = list(messages_dicts)
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "user":
                    msgs[i] = dict(msgs[i])
                    msgs[i]["content"] = msgs[i]["content"] + instruction
                    break
            else:
                msgs.append({"role": "user", "content": instruction})
        else:
            msgs = list(messages_dicts)

        # Ensure a system message exists — the Codex Responses endpoint
        # returns 400 "Instructions are required" when no system role
        # message is present.  Some Graphiti internal calls omit it.
        has_system = any(m.get("role") == "system" for m in msgs)
        if not has_system:
            msgs.insert(0, {
                "role": "system",
                "content": (
                    "You are a knowledge graph extraction assistant. "
                    "Follow the user's instructions precisely."
                ),
            })

        delay = _INITIAL_BACKOFF
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = self._client().chat_json(msgs)
                return result
            except Exception as exc:
                last_exc = exc
                msg_lower = str(exc).lower()
                is_rate_limit = (
                    "rate limit" in msg_lower
                    or "ratelimit" in msg_lower
                    or "429" in msg_lower
                    or "too many requests" in msg_lower
                    or "quota" in msg_lower
                )
                is_auth = "401" in msg_lower or "403" in msg_lower or "authentication" in msg_lower

                if is_auth:
                    # Token may have expired; refresh and retry once
                    logger.warning(
                        f"[GraphitiCodexLLMClient] Auth error on attempt {attempt + 1}: {exc}. "
                        "Refreshing token…"
                    )
                    self._codex_client = None  # Force re-resolve next call
                    if attempt == 0:
                        continue
                    raise

                if not is_rate_limit:
                    logger.error(
                        f"[GraphitiCodexLLMClient] Non-rate-limit error on attempt "
                        f"{attempt + 1}: {exc}"
                    )
                    raise

                if attempt >= _MAX_RETRIES:
                    logger.error(
                        f"[GraphitiCodexLLMClient] Rate limit: exhausted {_MAX_RETRIES} retries. "
                        f"Last error: {exc}"
                    )
                    raise

                jitter = random.uniform(0.5, 1.5)
                wait = min(delay * jitter, _MAX_BACKOFF)
                logger.warning(
                    f"[GraphitiCodexLLMClient] Rate limit hit (attempt {attempt + 1}/"
                    f"{_MAX_RETRIES + 1}). Waiting {wait:.1f}s… — {exc}"
                )
                time.sleep(wait)
                delay = min(delay * _BACKOFF_FACTOR, _MAX_BACKOFF)

        raise last_exc or RuntimeError("Codex call failed after retries")

    # ---------------------------------------------------------------------------
    # Graphiti LLMClient interface
    # ---------------------------------------------------------------------------

    async def _generate_response(
        self,
        messages,
        response_model=None,
        max_tokens: int = 16384,
        model_size=None,
    ) -> dict[str, Any]:
        """
        Graphiti abstract method implementation.

        Converts Graphiti Message objects → dicts, paces calls, then
        delegates to _call_codex_sync() in an executor thread (the sync
        HTTP call would block the event loop otherwise).
        """
        # Convert Message objects to dicts
        msgs_dicts = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        sem = self._get_semaphore()
        async with sem:
            # Pace: ensure minimum inter-call gap
            now = time.monotonic()
            elapsed = now - GraphitiCodexLLMClient._last_call_ts
            if elapsed < _MIN_DELAY:
                await asyncio.sleep(_MIN_DELAY - elapsed)
            GraphitiCodexLLMClient._last_call_ts = time.monotonic()

            # Run sync HTTP call in thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._call_codex_sync(msgs_dicts, response_model),
            )
            return result

    async def generate_response(
        self,
        messages,
        response_model=None,
        max_tokens=None,
        model_size=None,
        group_id=None,
        prompt_name=None,
    ) -> dict[str, Any]:
        """
        Top-level entry point called by Graphiti.

        Adds multilingual extraction instruction (mirrors Graphiti's own
        get_extraction_language_instruction() call) and delegates.
        """
        from graphiti_core.llm_client.client import get_extraction_language_instruction
        # Add multilingual extraction instruction if messages exist
        if messages:
            messages[0].content += get_extraction_language_instruction(group_id)

        return await self._generate_response(
            messages,
            response_model=response_model,
            max_tokens=max_tokens or self.max_tokens,
            model_size=model_size,
        )

    def __repr__(self) -> str:
        return (
            f"GraphitiCodexLLMClient("
            f"model={self.model!r}, "
            f"concurrency={_CONCURRENCY}, "
            f"min_delay={_MIN_DELAY}s, "
            f"max_retries={_MAX_RETRIES})"
        )
