"""Runtime-selectable modeling settings with lightweight persistence."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from ..config import Config
from .modeling_backend import ModelingBackendSelector

SETTINGS_FILE = os.path.join(Config.UPLOAD_FOLDER, "runtime_settings.json")

SUPPORTED_BACKENDS = {"ollama", "api_key", "codex", "openclaw"}
PERSISTED_SETTING_KEYS = {
    "modeling_backend",
    "llm_base_url",
    "llm_model_name",
    "codex_model_name",
    "openclaw_provider",
    "openclaw_model",
    "embedding_base_url",
    "embedding_model",
}

ENV_TO_CONFIG_ATTR = {
    "modeling_backend": ("MODELING_BACKEND", "MODELING_BACKEND"),
    "llm_base_url": ("LLM_BASE_URL", "LLM_BASE_URL"),
    "llm_model_name": ("LLM_MODEL_NAME", "LLM_MODEL_NAME"),
    "codex_model_name": ("CODEX_MODEL_NAME", "CODEX_MODEL_NAME"),
    "openclaw_provider": ("OPENCLAW_PROVIDER", "OPENCLAW_PROVIDER"),
    "openclaw_model": ("OPENCLAW_MODEL", "OPENCLAW_MODEL"),
    "embedding_base_url": ("EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL"),
    "embedding_model": ("EMBEDDING_MODEL", "EMBEDDING_MODEL"),
}


def _normalize_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _normalize_backend(value: Any) -> str:
    normalized = (_normalize_optional_string(value) or Config.MODELING_BACKEND).lower()
    if normalized not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unsupported modeling_backend {normalized!r}. "
            f"Expected one of: {', '.join(sorted(SUPPORTED_BACKENDS))}."
        )
    return normalized


def _ensure_settings_dir() -> None:
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)


def _read_settings_file() -> Dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return {}

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}

    return {
        key: payload.get(key)
        for key in PERSISTED_SETTING_KEYS
        if key in payload
    }


def _write_settings_file(settings: Dict[str, Any]) -> None:
    _ensure_settings_dir()
    payload = dict(settings)
    payload["updated_at"] = datetime.now().isoformat()

    with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _set_runtime_value(key: str, value: Optional[str]) -> None:
    env_name, config_attr = ENV_TO_CONFIG_ATTR[key]

    if value is None and key in {"openclaw_provider", "openclaw_model"}:
        os.environ.pop(env_name, None)
        setattr(Config, config_attr, None)
        return

    if value is None:
        return

    os.environ[env_name] = value
    setattr(Config, config_attr, value)


def _ensure_runtime_defaults() -> None:
    if Config.MODELING_BACKEND == "ollama" and not Config.LLM_API_KEY:
        os.environ.setdefault("LLM_API_KEY", "ollama")
        Config.LLM_API_KEY = os.environ["LLM_API_KEY"]


def _derive_ollama_tags_url(openai_base_url: Optional[str]) -> str:
    base_url = _normalize_optional_string(openai_base_url) or "http://127.0.0.1:11434/v1"

    if base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/")[:-3]

    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    tags_path = f"{path}/api/tags" if path else "/api/tags"
    return parsed._replace(path=tags_path, params="", query="", fragment="").geturl()


def get_ollama_status(base_url: Optional[str] = None) -> Dict[str, Any]:
    tags_url = _derive_ollama_tags_url(base_url or Config.LLM_BASE_URL)
    configured_base_url = _normalize_optional_string(base_url) or Config.LLM_BASE_URL

    try:
        response = requests.get(tags_url, timeout=1.5)
        response.raise_for_status()
        payload = response.json()
        model_names = [
            model.get("name")
            for model in payload.get("models", [])
            if model.get("name")
        ]
        return {
            "configured_base_url": configured_base_url,
            "tags_url": tags_url,
            "reachable": True,
            "available_models": model_names,
            "error": None,
        }
    except Exception as exc:
        return {
            "configured_base_url": configured_base_url,
            "tags_url": tags_url,
            "reachable": False,
            "available_models": [],
            "error": str(exc),
        }


def get_modeling_settings() -> Dict[str, Any]:
    return {
        "modeling_backend": Config.MODELING_BACKEND,
        "llm_base_url": Config.LLM_BASE_URL,
        "llm_model_name": Config.LLM_MODEL_NAME,
        "codex_model_name": Config.CODEX_MODEL_NAME,
        "openclaw_provider": Config.OPENCLAW_PROVIDER,
        "openclaw_model": Config.OPENCLAW_MODEL,
        "embedding_base_url": Config.EMBEDDING_BASE_URL,
        "embedding_model": Config.EMBEDDING_MODEL,
    }


def get_saved_modeling_settings() -> Dict[str, Any]:
    return _read_settings_file()


def apply_runtime_settings(settings: Dict[str, Any], *, persist: bool = True) -> Dict[str, Any]:
    normalized_updates: Dict[str, Any] = {}

    for key in PERSISTED_SETTING_KEYS:
        if key not in settings:
            continue

        if key == "modeling_backend":
            normalized_updates[key] = _normalize_backend(settings[key])
        elif key in {"openclaw_provider", "openclaw_model"}:
            normalized_updates[key] = _normalize_optional_string(settings[key])
        else:
            normalized_value = _normalize_optional_string(settings[key])
            if normalized_value is None:
                raise ValueError(f"{key} must not be empty when provided.")
            normalized_updates[key] = normalized_value

    for key, value in normalized_updates.items():
        _set_runtime_value(key, value)

    _ensure_runtime_defaults()
    ModelingBackendSelector.reset()

    if persist and normalized_updates:
        saved = _read_settings_file()
        saved.update(normalized_updates)
        _write_settings_file(saved)

    return get_modeling_settings()


def load_runtime_settings() -> Dict[str, Any]:
    saved = _read_settings_file()
    if saved:
        return apply_runtime_settings(saved, persist=False)

    _ensure_runtime_defaults()
    return get_modeling_settings()
