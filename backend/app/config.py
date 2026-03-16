"""
Configuration — unified from .env

MiroFish Local supports three execution modes:

  1. Ollama mode  (MODELING_BACKEND=ollama or api_key with Ollama URL)
     Fully offline. Uses local Ollama for LLM + embeddings.

  2. API key mode  (MODELING_BACKEND=api_key)
     Any OpenAI-compatible API (OpenAI, Azure, DashScope, etc.)

  3. Codex mode  (MODELING_BACKEND=codex)
     Uses OpenAI Codex OAuth token via ChatGPT backend.
     Requires OpenClaw with openai-codex OAuth login.
"""

import os
from dotenv import load_dotenv

# Load .env from project root
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    load_dotenv(override=True)


class Config:
    """Flask configuration"""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    JSON_AS_ASCII = False

    # ===== Modeling Backend Selector =====
    #
    #   ollama   — local Ollama (default). LLM_API_KEY can be anything.
    #   api_key  — any OpenAI-compatible API with a real key.
    #   codex    — OpenAI Codex OAuth via OpenClaw bridge.
    #
    MODELING_BACKEND = os.environ.get('MODELING_BACKEND', 'ollama').strip().lower()

    # ===== LLM Configuration (OpenAI-compatible format) =====
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'qwen2.5:32b')

    # Codex-mode model (separate default so Codex doesn't inherit a cheap model)
    CODEX_MODEL_NAME = os.environ.get('CODEX_MODEL_NAME', 'gpt-5.4')

    # OAuth configuration (for future full OAuth PKCE flow)
    OPENAI_CLIENT_ID = os.environ.get('OPENAI_CLIENT_ID')
    OPENAI_CLIENT_SECRET = os.environ.get('OPENAI_CLIENT_SECRET')
    OPENAI_REDIRECT_URI = os.environ.get(
        'OPENAI_REDIRECT_URI', 'http://localhost:5001/api/auth/openai/callback'
    )

    # Neo4j
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'mirofish')

    # Embedding (Ollama)
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')
    EMBEDDING_BASE_URL = os.environ.get('EMBEDDING_BASE_URL', 'http://localhost:11434')

    # File upload
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing
    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 50

    # OASIS simulation
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]

    # Report Agent
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def validate(cls):
        """Validate required configuration."""
        errors = []

        if cls.MODELING_BACKEND == 'api_key' and not cls.LLM_API_KEY:
            errors.append(
                "LLM_API_KEY is required when MODELING_BACKEND=api_key. "
                "Set it in .env or switch to MODELING_BACKEND=ollama."
            )
        elif cls.MODELING_BACKEND == 'ollama' and not cls.LLM_API_KEY:
            # Ollama mode: API key can be any non-empty string
            errors.append(
                "LLM_API_KEY must be set (any non-empty value, e.g. 'ollama') "
                "when MODELING_BACKEND=ollama."
            )
        elif cls.MODELING_BACKEND == 'codex' and not cls.LLM_API_KEY:
            import logging
            logging.getLogger("mirofish.config").warning(
                "MODELING_BACKEND=codex and LLM_API_KEY is not set. "
                "Ensure an OAuth token has been stored via OpenClaw bridge "
                "or POST /api/auth/openai/credential before making LLM calls."
            )

        if not cls.NEO4J_URI:
            errors.append("NEO4J_URI is required")
        if not cls.NEO4J_PASSWORD:
            errors.append("NEO4J_PASSWORD is required")

        return errors
