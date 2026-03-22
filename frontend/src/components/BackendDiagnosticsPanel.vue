<template>
  <section class="diagnostics-card">
    <div class="diagnostics-header">
      <div>
        <p class="diagnostics-kicker">Connection and Model Diagnostics</p>
        <h2 class="diagnostics-title">Backend readiness</h2>
      </div>
      <button class="refresh-btn" :disabled="loading || syncingCodex || savingConfig" @click="loadDiagnostics">
        {{ syncingCodex ? 'Syncing…' : loading ? 'Refreshing…' : 'Refresh' }}
      </button>
    </div>

    <div class="diagnostics-badges">
      <span class="status-pill" :class="backendReachable ? 'ok' : 'error'">
        {{ backendReachable ? 'Backend reachable' : 'Backend unreachable' }}
      </span>
      <span class="status-pill neutral">
        API {{ connection.mode === 'same-origin' ? 'same-origin' : 'explicit target' }}
      </span>
      <span class="status-pill neutral">
        Mode: {{ backendModeLabel }}
      </span>
      <span class="status-pill" :class="offlineReady ? 'ok' : 'warn'">
        {{ offlineReady ? 'Offline-capable path available' : 'Network-backed path active' }}
      </span>
    </div>

    <p v-if="error" class="diagnostics-error">{{ error }}</p>
    <p v-if="saveMessage" class="save-message ok">{{ saveMessage }}</p>
    <p v-if="saveError" class="save-message error">{{ saveError }}</p>

    <div class="diagnostics-grid">
      <div class="diagnostics-item">
        <span class="item-label">Browser origin</span>
        <span class="item-value mono">{{ connection.browserOrigin || 'Unavailable' }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">API target</span>
        <span class="item-value mono">{{ apiTargetLabel }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">Backend origin</span>
        <span class="item-value mono">{{ modelingStatus?.request_origin || connection.effectiveApiOrigin || 'Unavailable' }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">OpenAI adapter</span>
        <span class="item-value mono">{{ modelingStatus?.openai_compat_base_url || adapterEndpoint }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">Configured LLM endpoint</span>
        <span class="item-value mono">{{ modelingStatus?.llm?.base_url || 'Unavailable' }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">Configured LLM model</span>
        <span class="item-value mono">{{ activeModelLabel }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">Embedding endpoint</span>
        <span class="item-value mono">{{ modelingStatus?.embedding?.base_url || 'Unavailable' }}</span>
      </div>
      <div class="diagnostics-item">
        <span class="item-label">Embedding model</span>
        <span class="item-value mono">{{ modelingStatus?.embedding?.model || 'Unavailable' }}</span>
      </div>
    </div>

    <div class="provider-section">
      <div class="provider-summary">
        <div class="summary-card">
          <span class="summary-label">Selected provider</span>
          <span class="summary-value">{{ activeProviderLabel }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Providers discovered</span>
          <span class="summary-value">{{ providers.length }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Providers with credentials</span>
          <span class="summary-value">{{ readyProviderCount }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Codex bridge</span>
          <span class="summary-value">{{ codexBridgeLabel }}</span>
        </div>
      </div>

      <div v-if="providers.length" class="provider-list">
        <article
          v-for="provider in providers"
          :key="provider.profile_key || provider.provider"
          class="provider-card"
          :class="{ active: isActiveProvider(provider.provider), unavailable: !provider.has_credential }"
        >
          <div class="provider-card-header">
            <div>
              <h3 class="provider-name">{{ provider.display_name || provider.provider }}</h3>
              <p class="provider-meta">{{ provider.provider }} · {{ provider.compat_mode || 'unknown' }}</p>
            </div>
            <span class="provider-badge" :class="provider.has_credential && provider.token_valid !== false ? 'ok' : 'warn'">
              {{ provider.has_credential ? provider.token_valid === false ? 'Credential expired' : 'Credential ready' : 'Missing credential' }}
            </span>
          </div>
          <p class="provider-endpoint mono">{{ provider.base_url || 'Custom or provider-native transport' }}</p>
          <p class="provider-model">
            Default model: <span class="mono">{{ provider.default_model || 'Provider default' }}</span>
          </p>
          <p v-if="provider.source_label || provider.source" class="provider-source">
            Source: <span class="mono">{{ provider.source_label || provider.source }}</span>
          </p>
          <p v-if="provider.notes" class="provider-notes">{{ provider.notes }}</p>
        </article>
      </div>

      <div v-else class="empty-state">
        No OpenClaw providers were discovered yet.
      </div>
    </div>

    <div class="recommendation-box">
      <p class="recommendation-title">Recommended next step</p>
      <p class="recommendation-text">{{ recommendation }}</p>
      <p v-if="profilesPath" class="recommendation-subtle mono">Profiles: {{ profilesPath }}</p>
    </div>

    <div v-if="showCodexSyncAction" class="codex-action-row">
      <button class="action-btn secondary compact" :disabled="syncingCodex" @click="handleCodexSync">
        {{ syncingCodex ? 'Syncing Codex token…' : 'Sync Codex Token from OpenClaw' }}
      </button>
      <span class="action-hint">Use this when OpenClaw already has a valid `openai-codex` login but MiroClaw has not imported it yet.</span>
    </div>

    <div class="config-section">
      <div class="section-header">
        <div>
          <p class="section-kicker">Remote OpenClaw</p>
          <h3 class="section-title">Managed profile import</h3>
        </div>
        <span class="section-note">Paste `auth-profiles.json` from the other machine. MiroClaw will store and use it locally for OpenClaw and Codex mode.</span>
      </div>

      <p v-if="importMessage" class="save-message ok">{{ importMessage }}</p>
      <p v-if="importError" class="save-message error">{{ importError }}</p>

      <div class="managed-summary">
        <div class="summary-card">
          <span class="summary-label">Managed import</span>
          <span class="summary-value">{{ managedImportFound ? 'Present' : 'Not imported yet' }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Managed providers</span>
          <span class="summary-value">{{ managedProviderCount }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Import source</span>
          <span class="summary-value">{{ managedSourceLabel }}</span>
        </div>
        <div class="summary-card">
          <span class="summary-label">Codex source</span>
          <span class="summary-value">{{ codexSourceLabel }}</span>
        </div>
      </div>

      <div class="config-form import-form">
        <div class="field-grid">
          <label class="field">
            <span class="field-label">Import label</span>
            <input v-model="importSourceLabel" class="field-input" type="text" placeholder="Remote OpenClaw machine" />
            <span class="field-hint">Use this to remember which machine or account the imported profile bundle came from.</span>
          </label>

          <label class="field">
            <span class="field-label">Load from file</span>
            <input
              class="field-input"
              type="file"
              accept=".json,application/json"
              @change="handleImportFileSelection"
            />
            <span class="field-hint">
              Choose `auth-profiles.json` from the remote machine to fill the import box automatically.
            </span>
            <span v-if="importFileName" class="field-hint mono">Loaded file: {{ importFileName }}</span>
          </label>

          <div class="field field-surface">
            <span class="field-label">Current managed status</span>
            <div class="surface-stack">
              <p class="surface-copy" v-if="managedProfilesPath">
                Stored at <span class="mono">{{ managedProfilesPath }}</span>
              </p>
              <p class="surface-copy" v-if="managedUpdatedAt">
                Last updated {{ managedUpdatedAt }}
              </p>
              <p class="surface-copy" v-if="managedProviderNames.length">
                Providers: {{ managedProviderNames.join(', ') }}
              </p>
              <p class="surface-copy" v-if="!managedImportFound">
                No managed OpenClaw profile bundle is stored in this backend yet.
              </p>
            </div>
          </div>
        </div>

        <label class="field">
          <span class="field-label">Remote auth-profiles.json payload</span>
          <textarea
            v-model="importPayload"
            class="field-input field-textarea"
            placeholder='Paste the full auth-profiles.json JSON from the OpenClaw machine here'
          />
          <span class="field-hint">The full JSON file works best. If an `openai-codex` profile is included, MiroClaw will try to sync the Codex token automatically.</span>
        </label>

        <div class="form-actions">
          <button class="action-btn" :disabled="importingProfiles || loading" @click="handleImportProfiles">
            {{ importingProfiles ? 'Importing…' : 'Import OpenClaw Profiles' }}
          </button>
          <button class="action-btn secondary" :disabled="clearingImport || (!managedImportFound && !codexStatus?.bridge?.token_in_mirofish_store)" @click="handleClearManagedImport">
            {{ clearingImport ? 'Clearing…' : 'Clear Managed Import' }}
          </button>
        </div>
      </div>
    </div>

    <div class="config-section">
      <div class="section-header">
        <div>
          <p class="section-kicker">Live modeling selection</p>
          <h3 class="section-title">Runtime configuration</h3>
        </div>
        <span class="section-note">New prep and simulation runs use these settings immediately after you save.</span>
      </div>

      <div class="config-form">
        <label class="field">
          <span class="field-label">Modeling backend</span>
          <select v-model="form.modeling_backend" class="field-input">
            <option v-for="option in backendOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
          <span class="field-hint">{{ backendDescription }}</span>
        </label>

        <div class="field-grid">
          <label class="field">
            <span class="field-label">LLM API base URL</span>
            <input v-model="form.llm_base_url" class="field-input" type="text" placeholder="http://localhost:11434/v1" />
            <span class="field-hint">
              {{ backendHelp.llm_base_url }}
            </span>
          </label>

          <label class="field">
            <span class="field-label">LLM model name</span>
            <input
              v-model="form.llm_model_name"
              class="field-input"
              type="text"
              placeholder="qwen2.5:32b"
              :list="llmModelListId"
            />
            <span class="field-hint">
              {{ backendHelp.llm_model_name }}
            </span>
            <div v-if="ollamaModelSuggestions.length" class="chip-row">
              <button
                v-for="model in ollamaModelSuggestions.slice(0, 8)"
                :key="model"
                type="button"
                class="chip-btn"
                @click="form.llm_model_name = model"
              >
                {{ model }}
              </button>
            </div>
          </label>
        </div>

        <div class="field-grid">
          <label class="field">
            <span class="field-label">Codex model name</span>
            <input v-model="form.codex_model_name" class="field-input" type="text" placeholder="gpt-5.4" />
            <span class="field-hint">{{ backendHelp.codex_model_name }}</span>
          </label>

          <label class="field">
            <span class="field-label">Embedding model</span>
            <input v-model="form.embedding_model" class="field-input" type="text" placeholder="nomic-embed-text" />
            <span class="field-hint">{{ backendHelp.embedding_model }}</span>
          </label>
        </div>

        <div class="field-grid">
          <label class="field">
            <span class="field-label">OpenClaw provider</span>
            <select v-model="form.openclaw_provider" class="field-input">
              <option value="">Auto-detect</option>
              <option
                v-for="provider in openclawProviderOptions"
                :key="provider.value"
                :value="provider.value"
              >
                {{ provider.label }}
              </option>
            </select>
            <span class="field-hint">{{ backendHelp.openclaw_provider }}</span>
          </label>

          <label class="field">
            <span class="field-label">OpenClaw model</span>
            <input
              v-model="form.openclaw_model"
              class="field-input"
              type="text"
              placeholder="Provider default"
              :list="openclawModelListId"
            />
            <span class="field-hint">{{ backendHelp.openclaw_model }}</span>
            <div v-if="openclawModelSuggestions.length" class="chip-row">
              <button
                v-for="model in openclawModelSuggestions.slice(0, 8)"
                :key="model"
                type="button"
                class="chip-btn"
                @click="form.openclaw_model = model"
              >
                {{ model }}
              </button>
            </div>
          </label>
        </div>

        <div class="field-grid">
          <label class="field">
            <span class="field-label">Embedding base URL</span>
            <input v-model="form.embedding_base_url" class="field-input" type="text" placeholder="http://localhost:11434" />
            <span class="field-hint">{{ backendHelp.embedding_base_url }}</span>
          </label>

          <div class="field field-surface">
            <span class="field-label">Quick model picks</span>
            <div class="surface-stack">
              <p class="surface-copy">
                Ollama models become click-to-fill suggestions when the local tags endpoint is reachable.
              </p>
              <p class="surface-copy">
                OpenClaw provider model suggestions follow the currently selected provider, or auto-detect if none is pinned.
              </p>
            </div>
          </div>
        </div>

        <div class="form-actions">
          <button class="action-btn" :disabled="savingConfig || loading" @click="handleSaveConfig">
            {{ savingConfig ? 'Applying…' : 'Save and Apply' }}
          </button>
          <button class="action-btn secondary" :disabled="savingConfig || loading" @click="reloadFromStatus">
            Reset from backend
          </button>
        </div>
      </div>
    </div>

    <datalist :id="llmModelListId">
      <option v-for="model in ollamaModelSuggestions" :key="`llm-${model}`" :value="model" />
    </datalist>
    <datalist :id="openclawModelListId">
      <option v-for="model in openclawModelSuggestions" :key="`openclaw-${model}`" :value="model" />
    </datalist>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { getApiConnectionInfo } from '../api'
import {
  clearManagedOpenClawImport,
  getCodexStatus,
  getModelingStatus,
  importOpenClawProfiles,
  getOpenClawProviders,
  getOpenClawStatus,
  syncCodexToken,
  updateModelingConfig
} from '../api/auth'

const emit = defineEmits(['log'])

const connection = getApiConnectionInfo()
const adapterEndpoint = connection.effectiveApiOrigin
  ? `${connection.effectiveApiOrigin}/v1`
  : 'Unavailable'

const backendOptions = [
  { value: 'ollama', label: 'Ollama' },
  { value: 'api_key', label: 'API key' },
  { value: 'codex', label: 'Codex' },
  { value: 'openclaw', label: 'OpenClaw' }
]

const llmModelListId = 'llm-model-suggestions'
const openclawModelListId = 'openclaw-model-suggestions'

const loading = ref(false)
const syncingCodex = ref(false)
const savingConfig = ref(false)
const importingProfiles = ref(false)
const clearingImport = ref(false)
const error = ref('')
const saveError = ref('')
const saveMessage = ref('')
const importError = ref('')
const importMessage = ref('')
const backendReachable = ref(false)
const modelingStatus = ref(null)
const openclawStatus = ref(null)
const codexStatus = ref(null)
const providers = ref([])
const importPayload = ref('')
const importSourceLabel = ref('Remote OpenClaw machine')
const importFileName = ref('')

const form = reactive({
  modeling_backend: 'ollama',
  llm_base_url: '',
  llm_model_name: '',
  codex_model_name: '',
  openclaw_provider: '',
  openclaw_model: '',
  embedding_base_url: '',
  embedding_model: ''
})

const backendModeLabel = computed(() => modelingStatus.value?.modeling_backend || 'Unknown')
const apiTargetLabel = computed(() => connection.configuredBaseUrl || 'Same origin (/api)')
const readyProviderCount = computed(() => providers.value.filter(provider => provider.has_credential).length)
const managedImportFound = computed(() => Boolean(openclawStatus.value?.bridge?.managed_profiles_found))
const managedProfilesPath = computed(() => openclawStatus.value?.bridge?.managed_profiles_path || '')
const managedProviderCount = computed(() => openclawStatus.value?.bridge?.managed_provider_count || 0)
const managedProviderNames = computed(() => openclawStatus.value?.bridge?.managed_provider_names || [])
const managedSourceLabel = computed(() => openclawStatus.value?.bridge?.managed_source_label || 'Local-only')
const managedUpdatedAt = computed(() => openclawStatus.value?.bridge?.managed_updated_at || '')
const codexSourceLabel = computed(() => codexStatus.value?.bridge?.profile_source_label || codexStatus.value?.bridge?.profile_source || 'Unavailable')

const normalizedProviderValue = (value) => {
  const text = (value ?? '').toString().trim()
  if (!text || text === '(auto-detect)') {
    return ''
  }
  return text
}

const normalizedModelOverride = (value) => {
  const text = (value ?? '').toString().trim()
  if (!text || text === '(provider default)') {
    return ''
  }
  return text
}

const copyStatusToForm = (status) => {
  const saved = status?.saved_settings || {}

  form.modeling_backend = saved.modeling_backend || status?.modeling_backend || 'ollama'
  form.llm_base_url = saved.llm_base_url ?? status?.llm?.base_url ?? ''
  form.llm_model_name = saved.llm_model_name ?? status?.llm?.model ?? ''
  form.codex_model_name = saved.codex_model_name ?? status?.codex?.model ?? ''
  form.openclaw_provider = normalizedProviderValue(saved.openclaw_provider ?? status?.openclaw?.provider)
  form.openclaw_model = normalizedModelOverride(saved.openclaw_model ?? status?.openclaw?.model)
  form.embedding_base_url = saved.embedding_base_url ?? status?.embedding?.base_url ?? ''
  form.embedding_model = saved.embedding_model ?? status?.embedding?.model ?? ''
}

const activeProviderLabel = computed(() => {
  const backendMode = modelingStatus.value?.modeling_backend

  if (backendMode === 'openclaw') {
    return openclawStatus.value?.openclaw_provider || modelingStatus.value?.openclaw?.provider || 'Auto-detect'
  }

  if (backendMode === 'codex') {
    return 'openai-codex'
  }

  if (backendMode === 'ollama') {
    return 'ollama'
  }

  return 'LLM API'
})

const activeModelLabel = computed(() => {
  const backendMode = modelingStatus.value?.modeling_backend

  if (backendMode === 'openclaw') {
    return openclawStatus.value?.openclaw_model || modelingStatus.value?.openclaw?.model || 'Provider default'
  }

  if (backendMode === 'codex') {
    return codexStatus.value?.codex_model || modelingStatus.value?.codex?.model || 'Configured in .env'
  }

  if (backendMode === 'ollama') {
    return modelingStatus.value?.llm?.model || 'Ollama default'
  }

  return modelingStatus.value?.llm?.model || 'Unavailable'
})

const codexBridgeLabel = computed(() => {
  if (!codexStatus.value?.bridge) {
    return 'Unavailable'
  }
  if (codexStatus.value.bridge.ready_for_codex_mode) {
    return 'Ready'
  }
  if (codexStatus.value.bridge.token_present) {
    return 'Token found'
  }
  return 'Not ready'
})

const offlineReady = computed(() => {
  const status = modelingStatus.value?.offline_readiness
  const backendMode = modelingStatus.value?.modeling_backend
  const provider = (openclawStatus.value?.openclaw_provider || '').toLowerCase()

  return Boolean(
    status?.backend_is_offline_first ||
    (backendMode === 'openclaw' && provider === 'ollama') ||
    (status?.ollama_llm_configured && status?.ollama_embeddings_configured)
  )
})

const profilesPath = computed(() => codexStatus.value?.bridge?.profiles_path || '')

const recommendation = computed(() => {
  if (openclawStatus.value?.recommendation) {
    return openclawStatus.value.recommendation
  }
  if (codexStatus.value?.recommendation) {
    return codexStatus.value.recommendation
  }
  return 'Refresh diagnostics after changing backend settings or provider credentials.'
})

const showCodexSyncAction = computed(() => {
  const bridge = codexStatus.value?.bridge

  return Boolean(
    bridge?.token_present &&
    !bridge?.token_in_mirofish_store
  )
})

const selectedOpenClawProvider = computed(() => {
  if (!providers.value.length) {
    return null
  }

  const explicit = providers.value.find(provider => provider.provider === form.openclaw_provider)
  if (explicit) {
    return explicit
  }

  return providers.value.find(provider => provider.has_credential) || providers.value[0] || null
})

const openclawProviderOptions = computed(() => {
  const uniqueProviders = new Map()

  providers.value.forEach((provider) => {
    const key = provider.provider || ''
    if (!key) {
      return
    }

    const existing = uniqueProviders.get(key)
    if (!existing || (!existing.has_credential && provider.has_credential)) {
      uniqueProviders.set(key, provider)
    }
  })

  return Array.from(uniqueProviders.values()).map(provider => ({
    value: provider.provider || '',
    label: `${provider.display_name || provider.provider} (${provider.has_credential ? 'ready' : 'missing credential'})`
  }))
})

const ollamaModelSuggestions = computed(() => modelingStatus.value?.ollama?.available_models || [])

const openclawModelSuggestions = computed(() => selectedOpenClawProvider.value?.supported_models || [])

const backendDescription = computed(() => {
  switch (form.modeling_backend) {
    case 'ollama':
      return 'Best for fully offline runs. LLM and embeddings can both point at local Ollama endpoints.'
    case 'api_key':
      return 'Use any OpenAI-compatible provider with a direct API key and base URL.'
    case 'codex':
      return 'Use the OpenClaw Codex token path for ChatGPT-backed runs.'
    case 'openclaw':
      return 'Select one of the discovered OpenClaw providers or leave it on auto-detect.'
    default:
      return 'Choose the backend that matches the provider you want new runs to use.'
  }
})

const backendHelp = computed(() => {
  const backendMode = form.modeling_backend
  const provider = selectedOpenClawProvider.value
  return {
    llm_base_url: backendMode === 'ollama'
      ? 'Usually http://localhost:11434/v1 for Ollama or another OpenAI-compatible endpoint.'
      : 'Base URL used by the active OpenAI-compatible backend or local adapter.',
    llm_model_name: backendMode === 'ollama' && ollamaModelSuggestions.value.length
      ? 'Click a suggestion below or type a local Ollama model name.'
      : 'Model name used by the active backend.',
    codex_model_name: 'Used when the backend is set to Codex.',
    openclaw_provider: provider
      ? `Current discovered provider: ${provider.display_name || provider.provider}.`
      : 'Choose a provider, or leave blank to auto-detect the first available credentialed provider.',
    openclaw_model: provider?.supported_models?.length
      ? `Suggested models for ${provider.display_name || provider.provider} are available below.`
      : 'Leave blank to use the provider default.',
    embedding_base_url: 'Embedding endpoint, commonly the Ollama base URL without /v1.',
    embedding_model: 'Embedding model used for vectorization and search.'
  }
})

const isActiveProvider = (providerName) => {
  const normalizedProvider = (providerName || '').toLowerCase()
  const activeProvider = (openclawStatus.value?.openclaw_provider || '').toLowerCase()

  if (modelingStatus.value?.modeling_backend === 'codex') {
    return normalizedProvider === 'openai-codex'
  }

  if (activeProvider === '(auto-detect)' || !activeProvider) {
    return normalizedProvider === providers.value.find(provider => provider.has_credential)?.provider?.toLowerCase()
  }

  return normalizedProvider === activeProvider
}

const loadDiagnostics = async () => {
  loading.value = true
  error.value = ''

  const [modelingResult, openclawResult, providersResult, codexResult] = await Promise.allSettled([
    getModelingStatus(),
    getOpenClawStatus(),
    getOpenClawProviders(true),
    getCodexStatus()
  ])

  const requiredFailures = []

  if (modelingResult.status === 'fulfilled') {
    modelingStatus.value = modelingResult.value.data
    backendReachable.value = true
    copyStatusToForm(modelingStatus.value)
  } else {
    requiredFailures.push(modelingResult.reason?.message || 'Modeling status request failed')
  }

  if (openclawResult.status === 'fulfilled') {
    openclawStatus.value = openclawResult.value.data
  } else {
    requiredFailures.push(openclawResult.reason?.message || 'OpenClaw status request failed')
  }

  if (providersResult.status === 'fulfilled') {
    providers.value = providersResult.value.data?.providers || []
  } else {
    providers.value = []
  }

  if (codexResult.status === 'fulfilled') {
    codexStatus.value = codexResult.value.data
  }

  if (requiredFailures.length > 0) {
    backendReachable.value = false
    error.value = requiredFailures.join(' ')
  }

  loading.value = false
}

const reloadFromStatus = async () => {
  await loadDiagnostics()
  emit('log', 'Runtime modeling settings reloaded from the backend.')
}

const buildConfigPayload = () => ({
  modeling_backend: form.modeling_backend,
  llm_base_url: form.llm_base_url,
  llm_model_name: form.llm_model_name,
  codex_model_name: form.codex_model_name,
  openclaw_provider: form.openclaw_provider,
  openclaw_model: form.openclaw_model,
  embedding_base_url: form.embedding_base_url,
  embedding_model: form.embedding_model
})

const handleSaveConfig = async () => {
  savingConfig.value = true
  saveError.value = ''
  saveMessage.value = ''

  try {
    const response = await updateModelingConfig(buildConfigPayload())
    const message = response.message || 'Modeling settings updated.'
    saveMessage.value = message
    emit('log', message)
    await loadDiagnostics()
  } catch (syncError) {
    saveError.value = syncError.message
    emit('log', `Modeling settings update failed: ${syncError.message}`)
  } finally {
    savingConfig.value = false
  }
}

const handleCodexSync = async () => {
  syncingCodex.value = true

  try {
    const response = await syncCodexToken()
    emit('log', response.message || 'Codex token synced successfully.')
    await loadDiagnostics()
  } catch (syncError) {
    emit('log', `Codex sync failed: ${syncError.message}`)
    error.value = syncError.message
  } finally {
    syncingCodex.value = false
  }
}

const handleImportProfiles = async () => {
  importingProfiles.value = true
  importError.value = ''
  importMessage.value = ''

  try {
    const payloadText = importPayload.value.trim()
    if (!payloadText) {
      throw new Error('Paste the remote auth-profiles.json payload before importing.')
    }

    const response = await importOpenClawProfiles({
      profiles_json: payloadText,
      source_label: importSourceLabel.value,
      replace: true,
      sync_codex: true
    })

    importMessage.value = response.message || 'OpenClaw profiles imported.'
    importPayload.value = ''
    importFileName.value = ''
    emit('log', importMessage.value)
    await loadDiagnostics()
  } catch (importFailure) {
    importError.value = importFailure.message
    emit('log', `OpenClaw import failed: ${importFailure.message}`)
  } finally {
    importingProfiles.value = false
  }
}

const handleImportFileSelection = async (event) => {
  const file = event?.target?.files?.[0]
  if (!file) {
    return
  }

  try {
    importError.value = ''
    const text = await file.text()
    importPayload.value = text
    importFileName.value = file.name
    if (!importSourceLabel.value || importSourceLabel.value === 'Remote OpenClaw machine') {
      importSourceLabel.value = file.name.replace(/\.json$/i, '') || 'Remote OpenClaw machine'
    }
    emit('log', `Loaded OpenClaw profile file: ${file.name}`)
  } catch (fileError) {
    importError.value = 'Could not read the selected file.'
    emit('log', `OpenClaw file load failed: ${fileError.message}`)
  } finally {
    if (event?.target) {
      event.target.value = ''
    }
  }
}

const handleClearManagedImport = async () => {
  clearingImport.value = true
  importError.value = ''
  importMessage.value = ''

  try {
    const response = await clearManagedOpenClawImport({
      clear_synced_codex: true
    })

    importMessage.value = response.message || 'Managed OpenClaw import cleared.'
    importFileName.value = ''
    emit('log', importMessage.value)
    await loadDiagnostics()
  } catch (clearFailure) {
    importError.value = clearFailure.message
    emit('log', `Clearing managed OpenClaw import failed: ${clearFailure.message}`)
  } finally {
    clearingImport.value = false
  }
}

onMounted(() => {
  loadDiagnostics()
})
</script>

<style scoped>
.diagnostics-card {
  background:
    radial-gradient(circle at top right, rgba(255, 122, 69, 0.12), transparent 34%),
    linear-gradient(135deg, #fff8f2 0%, #ffffff 100%);
  border: 1px solid #ffd8c7;
  border-radius: 14px;
  padding: 20px;
  box-shadow: 0 10px 30px rgba(255, 87, 34, 0.08);
}

.diagnostics-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.diagnostics-kicker,
.section-kicker {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #ff5722;
  font-weight: 700;
}

.diagnostics-title,
.section-title {
  margin: 0;
  font-size: 28px;
  line-height: 1.1;
  color: #171717;
}

.refresh-btn,
.action-btn {
  border: 1px solid #171717;
  background: #171717;
  color: #fff;
  border-radius: 999px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.refresh-btn:disabled,
.action-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn.secondary {
  background: #fff;
  color: #171717;
}

.diagnostics-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border-radius: 999px;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.status-pill.ok,
.save-message.ok {
  background: #ecfdf3;
  color: #166534;
}

.status-pill.warn {
  background: #fff7ed;
  color: #9a3412;
}

.status-pill.error,
.save-message.error {
  background: #fef2f2;
  color: #b91c1c;
}

.status-pill.neutral {
  background: #f5f5f5;
  color: #404040;
}

.diagnostics-error {
  margin: 16px 0 0;
  color: #b91c1c;
  font-size: 14px;
  line-height: 1.5;
}

.save-message {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
}

.diagnostics-grid {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.diagnostics-item,
.summary-card,
.field-surface {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid #f3e0d7;
  border-radius: 12px;
  padding: 12px 14px;
  min-width: 0;
}

.item-label,
.summary-label,
.field-label {
  display: block;
  margin-bottom: 6px;
  color: #737373;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
}

.item-value,
.summary-value {
  color: #171717;
  font-size: 14px;
  line-height: 1.45;
  word-break: break-word;
}

.mono {
  font-family: 'JetBrains Mono', monospace;
}

.provider-section,
.config-section {
  margin-top: 18px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 12px;
}

.section-note {
  max-width: 360px;
  color: #7c2d12;
  font-size: 13px;
  line-height: 1.5;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 999px;
  padding: 8px 12px;
}

.provider-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.provider-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.provider-card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e7e5e4;
  border-radius: 12px;
  padding: 14px;
}

.provider-card.active {
  border-color: #ff7a45;
  box-shadow: 0 0 0 1px rgba(255, 122, 69, 0.2);
}

.provider-card.unavailable {
  opacity: 0.75;
}

.provider-card-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.provider-name {
  margin: 0;
  font-size: 17px;
  color: #171717;
}

.provider-meta {
  margin: 4px 0 0;
  color: #737373;
  font-size: 12px;
}

.provider-badge {
  flex-shrink: 0;
  align-self: flex-start;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 11px;
  font-weight: 700;
}

.provider-badge.ok {
  background: #ecfdf3;
  color: #166534;
}

.provider-badge.warn {
  background: #fff7ed;
  color: #9a3412;
}

.provider-endpoint,
.provider-model,
.provider-source,
.provider-notes {
  margin: 12px 0 0;
  color: #404040;
  font-size: 13px;
  line-height: 1.5;
}

.empty-state {
  margin-top: 14px;
  padding: 18px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px dashed #d6d3d1;
  color: #737373;
}

.recommendation-box {
  margin-top: 18px;
  padding: 16px;
  border-radius: 12px;
  background: #171717;
  color: #fafaf9;
}

.recommendation-title {
  margin: 0 0 6px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #fdba74;
  font-weight: 700;
}

.recommendation-text,
.recommendation-subtle {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
}

.recommendation-subtle {
  margin-top: 8px;
  color: #d6d3d1;
}

.codex-action-row {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.action-hint {
  font-size: 13px;
  color: #57534e;
  line-height: 1.5;
}

.action-btn.compact {
  min-width: 0;
  padding: 12px 16px;
}

.config-section {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid #f3e0d7;
}

.managed-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.config-form {
  display: grid;
  gap: 14px;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-input {
  width: 100%;
  border: 1px solid #d6d3d1;
  border-radius: 12px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.94);
  color: #171717;
  font-size: 14px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.field-textarea {
  min-height: 220px;
  resize: vertical;
  font-family: 'JetBrains Mono', monospace;
  line-height: 1.5;
}

.field-input:focus {
  outline: none;
  border-color: #ff7a45;
  box-shadow: 0 0 0 3px rgba(255, 122, 69, 0.12);
}

.field-hint {
  color: #737373;
  font-size: 12px;
  line-height: 1.5;
}

.field-surface {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
}

.surface-stack {
  display: grid;
  gap: 10px;
}

.surface-copy {
  margin: 0;
  color: #57534e;
  font-size: 13px;
  line-height: 1.5;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-btn {
  border: 1px solid #fed7aa;
  background: #fff7ed;
  color: #9a3412;
  border-radius: 999px;
  padding: 8px 11px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.chip-btn:hover {
  border-color: #fb923c;
}

.form-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-start;
}

@media (max-width: 900px) {
  .diagnostics-header,
  .section-header {
    flex-direction: column;
  }

  .refresh-btn {
    width: 100%;
  }

  .section-note {
    width: 100%;
    max-width: none;
  }
}
</style>
