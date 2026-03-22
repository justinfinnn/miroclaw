"""
Managed OpenClaw Profile Store
==============================

Persists imported OpenClaw auth profiles inside MiroClaw so the backend can
use provider credentials even when OpenClaw is running on a different machine.

The stored payload intentionally mirrors OpenClaw's ``auth-profiles.json``
shape: a top-level ``profiles`` mapping keyed by ``provider:profile`` names.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("mirofish.managed_openclaw_store")

_STORE_DIR = os.path.join(Config.UPLOAD_FOLDER, "credentials")
_STORE_FILE = os.path.join(_STORE_DIR, "openclaw_profiles.json")
_lock = threading.Lock()


class ManagedOpenClawProfileStore:
    """Thread-safe persistence for imported OpenClaw profile bundles."""

    @classmethod
    def file_path(cls) -> str:
        return _STORE_FILE

    @classmethod
    def _read_file(cls) -> Dict[str, Any]:
        if not os.path.exists(_STORE_FILE):
            return {}

        try:
            with open(_STORE_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning(f"Could not read managed OpenClaw profiles: {exc}")
            return {}

        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _write_file(cls, payload: Dict[str, Any]) -> None:
        os.makedirs(_STORE_DIR, exist_ok=True)
        with open(_STORE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @classmethod
    def _normalize_profiles_payload(cls, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError("Imported OpenClaw payload is not valid JSON.") from exc

        if not isinstance(payload, dict):
            raise ValueError("Imported OpenClaw payload must be a JSON object.")

        if isinstance(payload.get("profiles"), dict):
            profiles = payload["profiles"]
            extra = {
                key: value
                for key, value in payload.items()
                if key != "profiles"
            }
        else:
            profiles = payload
            extra = {}

        if not isinstance(profiles, dict) or not profiles:
            raise ValueError("Imported OpenClaw payload must contain a non-empty profiles object.")

        normalized_profiles: Dict[str, Dict[str, Any]] = {}
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                raise ValueError(f"Profile {profile_key!r} must be a JSON object.")

            normalized = dict(profile)
            normalized.setdefault("provider", str(profile_key).split(":")[0])
            normalized_profiles[str(profile_key)] = normalized

        return {
            "profiles": normalized_profiles,
            **extra,
        }

    @classmethod
    def load_payload(cls) -> Dict[str, Any]:
        with _lock:
            payload = cls._read_file()
        return payload

    @classmethod
    def load_profiles(cls) -> Dict[str, Dict[str, Any]]:
        payload = cls.load_payload()
        profiles = payload.get("profiles", {})
        return profiles if isinstance(profiles, dict) else {}

    @classmethod
    def status(cls) -> Dict[str, Any]:
        payload = cls.load_payload()
        profiles = payload.get("profiles", {})
        providers = {
            (profile.get("provider") or str(profile_key).split(":")[0])
            for profile_key, profile in profiles.items()
            if isinstance(profile, dict)
        }
        return {
            "managed_profiles_found": bool(profiles),
            "profiles_path": _STORE_FILE if profiles else None,
            "profiles_count": len(profiles),
            "provider_count": len(providers),
            "provider_names": sorted(p for p in providers if p),
            "source_label": payload.get("source_label"),
            "updated_at": payload.get("updated_at"),
        }

    @classmethod
    def import_profiles(
        cls,
        payload: Any,
        *,
        source_label: Optional[str] = None,
        replace: bool = True,
    ) -> Dict[str, Any]:
        normalized = cls._normalize_profiles_payload(payload)

        with _lock:
            existing = cls._read_file()
            existing_profiles = existing.get("profiles", {}) if isinstance(existing.get("profiles"), dict) else {}
            incoming_profiles = normalized["profiles"]
            merged_profiles = dict(incoming_profiles) if replace else {**existing_profiles, **incoming_profiles}

            stored_payload = {
                **{
                    key: value
                    for key, value in normalized.items()
                    if key != "profiles"
                },
                "profiles": merged_profiles,
                "source_label": (source_label or "").strip() or normalized.get("source_label") or "Imported OpenClaw profiles",
                "updated_at": datetime.now().isoformat(),
            }
            cls._write_file(stored_payload)

        providers = sorted({
            profile.get("provider") or profile_key.split(":")[0]
            for profile_key, profile in merged_profiles.items()
        })
        summary = {
            "profiles_count": len(merged_profiles),
            "imported_profiles_count": len(incoming_profiles),
            "provider_count": len(providers),
            "providers": providers,
            "codex_profile_present": any("openai-codex" in key for key in merged_profiles),
            "replaced_existing": replace,
            "source_label": stored_payload["source_label"],
            "updated_at": stored_payload["updated_at"],
        }
        logger.info(
            "[ManagedOpenClawProfileStore] Imported %s profile(s) across %s provider(s)",
            summary["profiles_count"],
            summary["provider_count"],
        )
        return summary

    @classmethod
    def update_profile(cls, profile_key: str, updates: Dict[str, Any]) -> bool:
        with _lock:
            payload = cls._read_file()
            profiles = payload.get("profiles", {})
            if not isinstance(profiles, dict) or profile_key not in profiles:
                return False

            current = profiles.get(profile_key)
            if not isinstance(current, dict):
                return False

            current.update(updates)
            payload["profiles"] = profiles
            payload["updated_at"] = datetime.now().isoformat()
            cls._write_file(payload)
        return True

    @classmethod
    def clear(cls) -> bool:
        with _lock:
            if not os.path.exists(_STORE_FILE):
                return False
            os.remove(_STORE_FILE)
        logger.info("[ManagedOpenClawProfileStore] Cleared imported OpenClaw profiles")
        return True
