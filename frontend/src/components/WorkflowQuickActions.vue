<template>
  <section class="workflow-quick-actions" aria-label="Workflow quick actions">
    <div class="quick-actions-copy">
      <span class="quick-actions-kicker mono">Journey Controls</span>
      <div class="quick-actions-title-row">
        <h2 class="quick-actions-title">{{ currentStage.label }}</h2>
        <span class="current-pill">{{ currentStage.stepLabel }}</span>
      </div>
      <p class="quick-actions-hint">{{ stageHint }}</p>
      <div class="context-chips">
        <span v-if="projectId" class="context-chip">Project {{ shortId(projectId) }}</span>
        <span v-if="simulationId" class="context-chip">Simulation {{ shortId(simulationId) }}</span>
        <span v-if="reportId" class="context-chip">Report {{ shortId(reportId) }}</span>
      </div>
    </div>

    <div class="quick-actions-links">
      <RouterLink
        v-for="link in links"
        :key="link.key"
        :to="link.to"
        class="quick-link"
        :class="`quick-link--${link.tone}`"
      >
        <span class="quick-link-label">{{ link.label }}</span>
        <span class="quick-link-subtitle">{{ link.subtitle }}</span>
      </RouterLink>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

const props = defineProps({
  current: {
    type: String,
    required: true
  },
  projectId: {
    type: String,
    default: null
  },
  simulationId: {
    type: String,
    default: null
  },
  reportId: {
    type: String,
    default: null
  }
})

const stageMeta = {
  upload: {
    label: 'Upload your source files',
    stepLabel: 'Upload'
  },
  graph: {
    label: 'Review the graph build',
    stepLabel: 'Graph'
  },
  setup: {
    label: 'Prepare the simulation setup',
    stepLabel: 'Setup'
  },
  run: {
    label: 'Monitor the live simulation',
    stepLabel: 'Run'
  },
  report: {
    label: 'Inspect the prediction report',
    stepLabel: 'Report'
  },
  chat: {
    label: 'Interview the simulated world',
    stepLabel: 'Chat'
  }
}

const orderedSteps = ['upload', 'graph', 'setup', 'run', 'report', 'chat']

const currentStage = computed(() => stageMeta[props.current] || stageMeta.upload)

const stageHint = computed(() => {
  if (props.current === 'setup') {
    return props.reportId
      ? 'An existing report is available for this simulation, so you can jump straight back into analysis or chat.'
      : 'Finish environment prep here, then launch the run. Report and chat shortcuts unlock as soon as a report exists.'
  }

  if (props.current === 'run') {
    return props.reportId
      ? 'You can keep watching the run or jump into the linked report and follow-up chat at any time.'
      : 'Stay here to monitor the run. Once report generation starts, the report and chat shortcuts will appear here too.'
  }

  if (props.current === 'report') {
    return 'Use the shortcuts to bounce back to setup or the live run without losing your place in the report.'
  }

  if (props.current === 'chat') {
    return 'Keep interviewing agents here, and hop back to the report or run whenever you want more context.'
  }

  return 'Use these links to move through the workflow without backing out to history.'
})

const buildRoute = (key) => {
  if (key === 'upload') {
    return { name: 'Home' }
  }

  if (key === 'graph') {
    return props.projectId ? { name: 'Process', params: { projectId: props.projectId } } : null
  }

  if (key === 'setup') {
    return props.simulationId ? { name: 'Simulation', params: { simulationId: props.simulationId } } : null
  }

  if (key === 'run') {
    return props.simulationId ? { name: 'SimulationRun', params: { simulationId: props.simulationId } } : null
  }

  if (key === 'report') {
    return props.reportId ? { name: 'Report', params: { reportId: props.reportId } } : null
  }

  if (key === 'chat') {
    return props.reportId ? { name: 'Interaction', params: { reportId: props.reportId } } : null
  }

  return null
}

const toneFor = (key) => {
  const currentIndex = orderedSteps.indexOf(props.current)
  const linkIndex = orderedSteps.indexOf(key)

  if (linkIndex === currentIndex + 1) return 'primary'
  if (linkIndex < currentIndex) return 'secondary'
  return 'ghost'
}

const links = computed(() => {
  const catalog = [
    { key: 'upload', label: 'Home', subtitle: 'Start a new project' },
    { key: 'graph', label: 'Graph', subtitle: 'Return to graph build' },
    { key: 'setup', label: 'Setup', subtitle: 'Adjust personas and config' },
    { key: 'run', label: 'Run', subtitle: 'Monitor the simulation' },
    { key: 'report', label: 'Report', subtitle: 'Review generated analysis' },
    { key: 'chat', label: 'Chat', subtitle: 'Interview agents and survey' }
  ]

  return catalog
    .filter(link => link.key !== props.current)
    .map(link => ({
      ...link,
      to: buildRoute(link.key),
      tone: toneFor(link.key)
    }))
    .filter(link => link.to)
})

const shortId = (value) => {
  if (!value) return ''
  if (value.length <= 18) return value
  return `${value.slice(0, 10)}...${value.slice(-4)}`
}
</script>

<style scoped>
.workflow-quick-actions {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 24px;
  border-bottom: 1px solid #ececec;
  background:
    radial-gradient(circle at top left, rgba(245, 158, 11, 0.12), transparent 32%),
    linear-gradient(135deg, #fffdf8 0%, #ffffff 58%, #f7f7f7 100%);
}

.quick-actions-copy {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.quick-actions-kicker {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: #8a6a2f;
}

.quick-actions-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.quick-actions-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}

.current-pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(17, 24, 39, 0.08);
  color: #374151;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.quick-actions-hint {
  margin: 0;
  max-width: 760px;
  color: #4b5563;
  font-size: 13px;
  line-height: 1.5;
}

.context-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.context-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(17, 24, 39, 0.08);
  color: #475569;
  font-size: 11px;
  font-weight: 600;
}

.quick-actions-links {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-content: center;
  gap: 10px;
  min-width: 320px;
}

.quick-link {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 150px;
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid #d9d9d9;
  color: #1f2937;
  text-decoration: none;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  box-shadow: 0 1px 0 rgba(15, 23, 42, 0.04);
}

.quick-link:hover {
  transform: translateY(-1px);
  border-color: #111827;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
}

.quick-link--primary {
  background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
  border-color: #111827;
  color: #ffffff;
}

.quick-link--secondary {
  background: #ffffff;
}

.quick-link--ghost {
  background: #f8fafc;
}

.quick-link-label {
  font-size: 13px;
  font-weight: 700;
}

.quick-link-subtitle {
  font-size: 11px;
  opacity: 0.72;
}

@media (max-width: 1100px) {
  .workflow-quick-actions {
    flex-direction: column;
  }

  .quick-actions-links {
    justify-content: flex-start;
    min-width: 0;
  }
}

@media (max-width: 720px) {
  .workflow-quick-actions {
    padding: 16px;
  }

  .quick-actions-title {
    font-size: 18px;
  }

  .quick-link {
    min-width: calc(50% - 5px);
  }
}

@media (max-width: 560px) {
  .quick-link {
    min-width: 100%;
  }
}
</style>
