<template>
  <section class="panel run-pane">
    <div class="header">
      <div>
        <p class="label">Run scraper</p>
      </div>
      <div class="header-actions">
        <button class="ghost" type="button" @click="refreshStatus" :disabled="!currentRunId">
          Refresh status
        </button>
      </div>
    </div>

    <div class="quick-actions">
      <div class="qa-head">
        <div>
          <p class="label">Shortcuts</p>
          <p class="hint">Launch any command from the backend.</p>
        </div>
        <span class="hint subtle"></span>
      </div>
      <div class="qa-grid">
        <button
          v-for="action in quickActions"
          :key="action.mode"
          type="button"
          class="qa-card"
          :disabled="loading"
          @click="startCliRun(action.mode)"
        >
          <div class="qa-row">
            <span class="pill small mode" :class="modeClass(action.mode)">{{ modeLabel(action.mode) }}</span>
            <span class="hint" :class="{ bold: loadingMode === action.mode }">
              {{ loadingMode === action.mode ? "Starting…" : action.subhead }}
            </span>
          </div>
          <p class="qa-title">{{ action.title }}</p>
          <p class="qa-desc">{{ action.description }}</p>
        </button>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div v-if="status" class="status">
      <div class="status-row">
        <div>
          <p class="label">Run ID</p>
          <div class="id-row">
            <code>{{ status.run_id }}</code>
            <span class="pill small mode" :class="modeClass(status.mode)">{{ modeLabel(status.mode) }}</span>
          </div>
        </div>
        <span class="pill" :class="status.status">{{ status.status }}</span>
      </div>
      <p class="label">Log tail</p>
      <pre>{{ status.log_tail || "Waiting for output..." }}</pre>
    </div>

  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { getRunStatus, startRun } from "../api";
import type { CliMode, RunStatus } from "../types";

interface QuickAction {
  mode: CliMode;
  title: string;
  description: string;
  subhead: string;
}

const status = ref<RunStatus | null>(null);
const loading = ref(false);
const loadingMode = ref<CliMode | null>(null);
const error = ref<string | null>(null);
const quickActions: QuickAction[] = [
  {
    mode: "run-once",
    title: "",
    description: "Kick off the default job search using the keywords/locations below.",
    subhead: "",
  },
  {
    mode: "purge",
    title: "",
    description: "Apply rule-based filters, then run the AI purge for noisy postings.",
    subhead: "",
  },
  {
    mode: "parse-descriptions",
    title: "",
    description: "Fetch any missing descriptions and parse them with the LLM.",
    subhead: "",
  },
  {
    mode: "reset-ai-purge",
    title: "",
    description: "Clear AI purge flags so you can re-run the smart filter.",
    subhead: "",
  },
];

const currentRunId = computed(() => status.value?.run_id || "");

const startCliRun = async (mode: CliMode) => {
  loading.value = true;
  loadingMode.value = mode;
  error.value = null;
  try {
    status.value = await startRun({
      mode,
    });
    startPolling();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to start run.";
  } finally {
    loading.value = false;
    loadingMode.value = null;
  }
};

const triggerRun = async () => startCliRun("run-once");

const refreshStatus = async () => {
  if (!currentRunId.value) return;
  try {
    status.value = await getRunStatus(currentRunId.value);
    if (status.value.status !== "running") {
      stopPolling();
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load status.";
    stopPolling();
  }
};

const startPolling = () => {
  stopPolling();
  pollHandle = window.setInterval(refreshStatus, 1500);
};

const stopPolling = () => {
  if (pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
};

const modeLabel = (mode: CliMode) => {
  switch (mode) {
    case "run-once":
      return "Run";
    case "reset-ai-purge":
      return "Reset AI Purge";
    case "parse-descriptions":
      return "Descriptions";
    case "purge":
      return "Purge";
    default:
      return mode;
  }
};

const modeClass = (mode: CliMode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;

onMounted(() => {
  // no-op
});

onBeforeUnmount(() => stopPolling());

let pollHandle: number | null = null;
</script>

<style scoped>
.run-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.quick-actions {
  border: 1px dashed var(--border);
  border-radius: 14px;
  padding: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
}

.qa-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.qa-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}

.qa-card {
  text-align: left;
  border: 1px solid var(--border);
  background: #ffffff;
  border-radius: 12px;
  padding: 8px 10px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
  transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
}

.qa-card:hover:enabled {
  transform: translateY(-2px);
  border-color: #cbd5e1;
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.08);
}

.qa-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.qa-title {
  margin: 2px 0 2px;
  font-weight: 700;
  font-size: 15px;
}

.qa-desc {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

h2 {
  margin: 2px 0 0;
}

.run-pane .label {
  margin: 0 0 4px;
  display: inline-block;
}

.form {
  display: grid;
  gap: 12px;
}

label {
  display: grid;
  gap: 4px;
  font-weight: 600;
  color: #0f172a;
}

input,
select {
  width: 100%;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: #f8fafc;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.primary {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 700;
  box-shadow: 0 10px 30px rgba(14, 165, 233, 0.35);
}

.primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.hint {
  color: var(--muted);
  font-size: 13px;
  margin: 0;
}

.hint.subtle {
  font-size: 12px;
}

.hint.bold {
  color: #0f172a;
  font-weight: 600;
}

.error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 10px 12px;
  border-radius: 10px;
  font-weight: 600;
}

.status {
  border: 1px dashed var(--border);
  padding: 12px;
  border-radius: 12px;
  background: #f8fafc;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.id-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pill.running {
  background: #ecfeff;
  color: #0284c7;
}

.pill.succeeded {
  background: #ecfdf3;
  color: #15803d;
}

.pill.failed {
  background: #fef2f2;
  color: #b91c1c;
}

.pill.mode {
  border: none;
}

.pill.mode-run-once {
  background: #eef2ff;
  color: #4338ca;
}

.pill.mode-purge {
  background: #fff1f2;
  color: #be123c;
}

.pill.mode-reset-ai-purge {
  background: #f1f5f9;
  color: #0f172a;
}

.pill.mode-parse-descriptions {
  background: #ecfeff;
  color: #0ea5e9;
}

.pill.mode-db-summary {
  background: #fff7ed;
  color: #b45309;
}

.pill.mode-test {
  background: #f3e8ff;
  color: #7c3aed;
}

pre {
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 10px;
  padding: 12px;
  overflow: auto;
  max-height: 220px;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}

.row-top {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 2px;
  flex-wrap: wrap;
}

.id {
  font-size: 12px;
  color: var(--muted);
}
</style>
