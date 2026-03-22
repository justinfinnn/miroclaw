import service, { requestWithRetry } from './index'

/**
 * Start report generation
 * @param {Object} data - { simulation_id, force_regenerate? }
 */
export const generateReport = (data) => {
  return requestWithRetry(() => service.post('/api/report/generate', data), 3, 1000)
}

/**
 * Get report generation status
 * @param {Object|string} data - { task_id?, report_id?, simulation_id? } or a reportId string
 */
export const getReportStatus = (data) => {
  const payload = typeof data === 'string' ? { report_id: data } : (data || {})
  return service.post('/api/report/generate/status', payload)
}

/**
 * Request cooperative cancellation for report generation
 * @param {Object} data - { task_id?, report_id?, simulation_id? }
 */
export const cancelReportGeneration = (data) => {
  return service.post('/api/report/generate/cancel', data)
}

/**
 * Get Agent log (incremental)
 * @param {string} reportId
 * @param {number} fromLine - Start from which line
 */
export const getAgentLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/agent-log`, { params: { from_line: fromLine } })
}

/**
 * Get console log (incremental)
 * @param {string} reportId
 * @param {number} fromLine - Start from which line
 */
export const getConsoleLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/console-log`, { params: { from_line: fromLine } })
}

/**
 * Get report details
 * @param {string} reportId
 */
export const getReport = (reportId) => {
  return service.get(`/api/report/${reportId}`)
}

/**
 * Get the latest report for a simulation
 * @param {string} simulationId
 */
export const getReportBySimulation = (simulationId) => {
  return service.get(`/api/report/by-simulation/${simulationId}`)
}

/**
 * Chat with Report Agent
 * @param {Object} data - { simulation_id, message, chat_history? }
 */
export const chatWithReport = (data) => {
  return requestWithRetry(() => service.post('/api/report/chat', data), 3, 1000)
}
