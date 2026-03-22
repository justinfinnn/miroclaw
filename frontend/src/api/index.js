import axios from 'axios'

const resolveApiBaseUrl = () => {
  const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
  return configuredBaseUrl || ''
}

export const getApiConnectionInfo = () => {
  const configuredBaseUrl = resolveApiBaseUrl()
  const browserOrigin = typeof window !== 'undefined' ? window.location.origin : ''

  if (!configuredBaseUrl) {
    return {
      browserOrigin,
      configuredBaseUrl: '',
      effectiveApiOrigin: browserOrigin,
      mode: 'same-origin'
    }
  }

  const normalizedUrl = new URL(configuredBaseUrl, browserOrigin || 'http://localhost')

  return {
    browserOrigin,
    configuredBaseUrl,
    effectiveApiOrigin: normalizedUrl.origin,
    mode: 'explicit'
  }
}

// Create axios instance
const service = axios.create({
  baseURL: resolveApiBaseUrl(),
  timeout: 300000, // 5 minute timeout (ontology generation may require longer time)
  headers: {
    'Content-Type': 'application/json'
  }
})

// Request interceptor
service.interceptors.request.use(
  config => {
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// Response interceptor (fault-tolerant retry mechanism)
service.interceptors.response.use(
  response => {
    const res = response.data

    // If the returned status code is not success, throw error
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }

    return res
  },
  error => {
    console.error('Response error:', error)

    // Handle timeout
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }

    // Handle network error
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }

    return Promise.reject(error)
  }
)

// Request function with retry
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error

      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
