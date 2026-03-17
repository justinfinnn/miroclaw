# OpenClaw Provider Integration

MiroClaw can use **any LLM provider** configured in your local OpenClaw installation.

## Quick Setup

```bash
# In your .env:
MODELING_BACKEND=openclaw
OPENCLAW_PROVIDER=anthropic          # optional — auto-detects if unset
OPENCLAW_MODEL=claude-sonnet-4-6     # optional — uses provider default if unset
```

Restart the server. That's it.

## How It Works

1. MiroClaw reads `~/.openclaw/agents/<agent>/agent/auth-profiles.json`
2. Parses all provider entries (API keys, tokens, OAuth credentials)
3. Constructs the appropriate LLM client for the selected provider
4. Routes all LLM calls through that provider

## Supported Providers

| Provider | OpenClaw Name | Compat Mode | Status |
|----------|--------------|-------------|--------|
| **OpenAI** | `openai` | OpenAI SDK | ✅ Fully supported |
| **Anthropic (Claude)** | `anthropic` | Native SDK | ✅ Fully supported |
| **OpenAI Codex** | `openai-codex` | CodexClient | ✅ Fully supported |
| **Google Gemini** | `google-gemini-cli` | OpenAI-compat | ✅ Supported |
| **Moonshot (Kimi)** | `moonshot` | OpenAI-compat | ✅ Supported |
| **Alibaba Qwen** | `qwen-portal` | OpenAI-compat | ✅ Supported |
| **Ollama** | `ollama` | OpenAI-compat | ✅ Supported |
| **AWS Bedrock** | `bedrock` | — | ❌ Not yet supported |

## API Endpoints

### GET `/api/auth/openclaw/providers`

Lists all discovered OpenClaw providers and their capabilities.

```json
{
  "success": true,
  "data": {
    "providers": [
      {
        "provider": "anthropic",
        "type": "token",
        "has_credential": true,
        "display_name": "Anthropic (Claude)",
        "default_model": "claude-sonnet-4-20250514",
        "compat_mode": "anthropic",
        "token_valid": true
      }
    ],
    "openclaw_config": {
      "provider": "anthropic",
      "model": "(provider default)"
    }
  }
}
```

### GET `/api/auth/openclaw/status`

Diagnostic summary of the OpenClaw integration.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODELING_BACKEND` | `ollama` | Set to `openclaw` to enable |
| `OPENCLAW_PROVIDER` | _(auto)_ | Which provider to use |
| `OPENCLAW_MODEL` | _(auto)_ | Model name override |
| `OPENCLAW_AGENT` | _(auto)_ | Which OpenClaw agent's profiles to read |
| `OPENCLAW_AGENT_PROFILES` | _(auto)_ | Direct path to auth-profiles.json |

## Provider Discovery

The bridge searches for `auth-profiles.json` in this order:

1. `OPENCLAW_AGENT_PROFILES` env var (explicit path)
2. `~/.openclaw/agents/<OPENCLAW_AGENT>/agent/auth-profiles.json`
3. `~/.openclaw/agents/backend-architect/agent/auth-profiles.json`
4. First `~/.openclaw/agents/*/agent/auth-profiles.json` with profiles

## Architecture

```
MODELING_BACKEND=openclaw
        │
        ▼
ModelingBackend._build_openclaw_client()
        │
        ├── OpenClawBridge.discover_providers()
        │   └── reads auth-profiles.json
        │
        ├── Select provider (OPENCLAW_PROVIDER or first available)
        │
        └── Build client by compat_mode:
            ├── "openai"    → LLMClient (OpenAI SDK)
            ├── "codex"     → CodexClient (ChatGPT backend)
            ├── "anthropic" → AnthropicLLMClient (native SDK)
            └── "unknown"   → LLMClient (best-effort)
```

## Anthropic Support

For full Anthropic support, install the SDK:

```bash
pip install anthropic
```

Without it, MiroClaw falls back to the OpenAI SDK compatibility layer,
which may not support all Anthropic-specific features.

## Notes

- Existing backends (`ollama`, `api_key`, `codex`) are unchanged
- OpenClaw mode reads credentials at client construction time
- Credentials are never persisted by MiroClaw — they stay in OpenClaw's store
- Multiple providers can be available simultaneously; switch with `OPENCLAW_PROVIDER`
