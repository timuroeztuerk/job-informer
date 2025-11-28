<template>
  <section class="panel">
    <div class="header">
      <div>
        <p class="label">Run scraper</p>
        <h2>Kick off a fresh scrape</h2>
      </div>
      <button class="ghost" type="button" @click="refreshStatus" :disabled="!currentRunId">
        Refresh status
      </button>
    </div>

    <form class="form" @submit.prevent="triggerRun">
      <label>
        <span>Keywords (optional)</span>
        <input v-model="keywords" placeholder="Data Scientist, Data Analyst" />
      </label>
      <label>
        <span>Locations (optional)</span>
        <input v-model="locations" placeholder="Berlin, Zurich" />
      </label>
      <label>
        <span>Time range</span>
        <select v-model="timeRange">
          <option value="">Use backend default</option>
          <option value="day">Last day</option>
          <option value="week">Last week</option>
          <option value="month">Last month</option>
        </select>
      </label>
      <div class="actions">
        <button class="primary" type="submit" :disabled="loading">
          {{ loading ? "Starting..." : "Run main.py" }}
        </button>
        <span class="hint">Backend command: python main.py --run-once</span>
      </div>
    </form>

    <div v-if="error" class="error">{{ error }}</div>

    <div v-if="status" class="status">
      <div class="status-row">
        <div>
          <p class="label">Run ID</p>
          <code>{{ status.run_id }}</code>
        </div>
        <span class="pill" :class="status.status">{{ status.status }}</span>
      </div>
      <p class="label">Log tail</p>
      <pre>{{ status.log_tail || "Waiting for output..." }}</pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { getRunStatus, startRun } from "../api";
import type { RunStatus } from "../types";

const keywords = ref("");
const locations = ref("");
const timeRange = ref("");
const status = ref<RunStatus | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const currentRunId = computed(() => status.value?.run_id || "");
let pollHandle: number | null = null;

const triggerRun = async () => {
  loading.value = true;
  error.value = null;
  try {
    status.value = await startRun({
      keywords: keywords.value || undefined,
      locations: locations.value || undefined,
      time_range: timeRange.value || undefined,
    });
    startPolling();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to start run.";
  } finally {
    loading.value = false;
  }
};

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

onBeforeUnmount(() => stopPolling());
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

h2 {
  margin: 2px 0 0;
}

.label {
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 4px;
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

.ghost {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px 12px;
}

.hint {
  color: var(--muted);
  font-size: 13px;
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

.pill {
  border-radius: 999px;
  padding: 6px 10px;
  font-weight: 700;
  text-transform: capitalize;
  border: 1px solid var(--border);
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

pre {
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 10px;
  padding: 12px;
  overflow: auto;
  max-height: 200px;
  font-size: 13px;
}
</style>
