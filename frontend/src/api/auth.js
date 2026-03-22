import service from './index'

export const getModelingStatus = () => {
  return service.get('/api/auth/modeling/status')
}

export const updateModelingConfig = (data) => {
  return service.post('/api/auth/modeling/config', data)
}

export const getOpenAIStatus = () => {
  return service.get('/api/auth/openai/status')
}

export const getCodexStatus = () => {
  return service.get('/api/auth/codex/status')
}

export const syncCodexToken = (force = false) => {
  return service.post('/api/auth/codex/sync', { force })
}

export const getOpenClawStatus = () => {
  return service.get('/api/auth/openclaw/status')
}

export const getOpenClawProviders = (includeUnsupported = false) => {
  return service.get('/api/auth/openclaw/providers', {
    params: {
      include_unsupported: includeUnsupported
    }
  })
}

export const importOpenClawProfiles = (data) => {
  return service.post('/api/auth/openclaw/import', data)
}

export const clearManagedOpenClawImport = (data = {}) => {
  return service.delete('/api/auth/openclaw/import', {
    data
  })
}
