<template>
  <nav class="workflow-stepper" aria-label="Workflow navigation">
    <component
      :is="step.to ? RouterLink : 'span'"
      v-for="step in steps"
      :key="step.key"
      v-bind="step.to ? { to: step.to } : {}"
      class="step-chip"
      :class="{ current: current === step.key, disabled: !step.to && current !== step.key }"
      :aria-current="current === step.key ? 'step' : undefined"
      :title="step.to ? `Open ${step.label}` : `${step.label} is not available yet`"
    >
      <span class="step-number mono">{{ step.number }}</span>
      <span class="step-copy">
        <span class="step-label">{{ step.label }}</span>
        <span class="step-subtitle">{{ step.subtitle }}</span>
      </span>
    </component>
  </nav>
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

const steps = computed(() => [
  {
    key: 'upload',
    number: '01',
    label: 'Upload',
    subtitle: 'Start here',
    to: { name: 'Home' }
  },
  {
    key: 'graph',
    number: '02',
    label: 'Graph',
    subtitle: 'Build the graph',
    to: props.projectId ? { name: 'Process', params: { projectId: props.projectId } } : null
  },
  {
    key: 'setup',
    number: '03',
    label: 'Setup',
    subtitle: 'Prepare personas',
    to: props.simulationId ? { name: 'Simulation', params: { simulationId: props.simulationId } } : null
  },
  {
    key: 'run',
    number: '04',
    label: 'Run',
    subtitle: 'Launch simulation',
    to: props.simulationId ? { name: 'SimulationRun', params: { simulationId: props.simulationId } } : null
  },
  {
    key: 'report',
    number: '05',
    label: 'Report',
    subtitle: 'Review results',
    to: props.reportId ? { name: 'Report', params: { reportId: props.reportId } } : null
  },
  {
    key: 'chat',
    number: '06',
    label: 'Chat',
    subtitle: 'Interview agents',
    to: props.reportId ? { name: 'Interaction', params: { reportId: props.reportId } } : null
  }
])
</script>

<style scoped>
.workflow-stepper {
  display: flex;
  gap: 12px;
  padding: 14px 24px;
  border-bottom: 1px solid #ececec;
  background: linear-gradient(180deg, #ffffff 0%, #fafafa 100%);
  overflow-x: auto;
}

.step-chip {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 150px;
  padding: 12px 14px;
  border: 1px solid #dddddd;
  border-radius: 16px;
  background: #ffffff;
  color: #444444;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-decoration: none;
  transition: all 0.2s ease;
  white-space: nowrap;
  box-shadow: 0 1px 0 rgba(17, 24, 39, 0.03);
}

.step-chip:hover {
  border-color: #111111;
  color: #111111;
  transform: translateY(-1px);
}

.step-chip.current {
  background: linear-gradient(180deg, #111111 0%, #1f2937 100%);
  border-color: #111111;
  color: #ffffff;
  box-shadow: 0 12px 28px rgba(17, 24, 39, 0.18);
}

.step-chip.disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 999px;
  background: rgba(17, 17, 17, 0.06);
  color: inherit;
  font-size: 11px;
  flex-shrink: 0;
}

.step-chip.current .step-number {
  background: rgba(255, 255, 255, 0.14);
}

.step-copy {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
}

.step-label {
  font-size: 12px;
  line-height: 1.2;
}

.step-subtitle {
  font-size: 10px;
  font-weight: 500;
  line-height: 1.2;
  color: inherit;
  opacity: 0.72;
}
</style>
