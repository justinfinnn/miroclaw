import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.managed_openclaw_store import ManagedOpenClawProfileStore
from app.services.openclaw_bridge import OpenClawBridge


def _sample_profiles():
    return {
        "profiles": {
            "openai-codex:default": {
                "provider": "openai-codex",
                "type": "oauth",
                "access": "managed-access-token",
                "refresh": "managed-refresh-token",
                "expires": 4102444800000,
                "accountId": "acct_managed",
            },
            "anthropic:manual": {
                "provider": "anthropic",
                "type": "api_key",
                "key": "sk-ant-managed",
            },
        }
    }


@pytest.fixture()
def isolated_openclaw_storage(tmp_path, monkeypatch):
    import app.services.managed_openclaw_store as managed_store_module

    managed_dir = tmp_path / "credentials"
    managed_file = managed_dir / "openclaw_profiles.json"

    monkeypatch.setattr(managed_store_module, "_STORE_DIR", str(managed_dir))
    monkeypatch.setattr(managed_store_module, "_STORE_FILE", str(managed_file))

    ManagedOpenClawProfileStore.clear()
    yield tmp_path
    ManagedOpenClawProfileStore.clear()


def test_import_profiles_updates_status_and_counts(isolated_openclaw_storage):
    summary = ManagedOpenClawProfileStore.import_profiles(
        _sample_profiles(),
        source_label="Remote Machine A",
        replace=True,
    )

    status = ManagedOpenClawProfileStore.status()
    profiles = ManagedOpenClawProfileStore.load_profiles()

    assert summary["profiles_count"] == 2
    assert summary["provider_count"] == 2
    assert summary["codex_profile_present"] is True
    assert status["managed_profiles_found"] is True
    assert status["profiles_count"] == 2
    assert status["provider_names"] == ["anthropic", "openai-codex"]
    assert status["source_label"] == "Remote Machine A"
    assert set(profiles.keys()) == {"openai-codex:default", "anthropic:manual"}


def test_import_profiles_can_merge_without_replacing_existing(isolated_openclaw_storage):
    ManagedOpenClawProfileStore.import_profiles(
        _sample_profiles(),
        source_label="Remote Machine A",
        replace=True,
    )

    second_payload = {
        "profiles": {
            "google-gemini-cli:default": {
                "provider": "google-gemini-cli",
                "type": "api_key",
                "key": "gemini-key",
            }
        }
    }

    summary = ManagedOpenClawProfileStore.import_profiles(
        second_payload,
        source_label="Remote Machine B",
        replace=False,
    )

    profiles = ManagedOpenClawProfileStore.load_profiles()

    assert summary["profiles_count"] == 3
    assert summary["imported_profiles_count"] == 1
    assert summary["provider_count"] == 3
    assert set(profiles.keys()) == {
        "openai-codex:default",
        "anthropic:manual",
        "google-gemini-cli:default",
    }
    assert ManagedOpenClawProfileStore.status()["source_label"] == "Remote Machine B"


def test_clear_returns_false_when_store_missing(isolated_openclaw_storage):
    assert ManagedOpenClawProfileStore.clear() is False


def test_bridge_discovers_managed_profiles_and_prefers_managed_conflicts(isolated_openclaw_storage, tmp_path):
    ManagedOpenClawProfileStore.import_profiles(
        _sample_profiles(),
        source_label="Managed Import",
        replace=True,
    )

    local_profiles_path = tmp_path / "auth-profiles.json"
    local_profiles_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "openai-codex:default": {
                        "provider": "openai-codex",
                        "type": "oauth",
                        "access": "local-access-token",
                        "refresh": "local-refresh-token",
                        "expires": 4102444800000,
                        "accountId": "acct_local",
                    },
                    "google-gemini-cli:default": {
                        "provider": "google-gemini-cli",
                        "type": "api_key",
                        "key": "local-gemini-key",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bridge = OpenClawBridge()
    bridge._profiles_path = local_profiles_path

    providers = bridge.discover_providers()

    provider_map = {provider["profile_key"]: provider for provider in providers}

    assert set(provider_map.keys()) == {
        "openai-codex:default",
        "anthropic:manual",
        "google-gemini-cli:default",
    }
    assert provider_map["openai-codex:default"]["source"] == "managed"
    assert provider_map["openai-codex:default"]["credential"] == "managed-access-token"
    assert provider_map["openai-codex:default"]["source_label"] == "Managed Import"
    assert provider_map["google-gemini-cli:default"]["source"] == "local"
    assert provider_map["google-gemini-cli:default"]["credential"] == "local-gemini-key"
