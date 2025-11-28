<template>
  <section class="panel">
    <div class="header">
      <div>
        <p class="label">Recent runs</p>
        <h3>Latest activity</h3>
      </div>
      <button class="ghost" type="button" @click="load" :disabled="loading">Refresh</button>
    </div>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-else-if="loading" class="muted">Loading…</div>
    <div v-else-if="!runs.length" class="muted">No runs yet.</div>
    <div v-else class="list">
      <div v-for="run in runs" :key="run.run_id" class="row">
        <div class="top">
          <span class="pill small mode" :class="modeClass(run.mode)">{{ modeLabel(run.mode) }}</span>
          <span class="pill small" :class="run.status">{{ run.status }}</span>
        </div>
        <p class="muted small">{{ formatDate(run.started_at) }}</p>
        <p class="muted tiny">{{ run.run_id.slice(0, 10) }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { listRuns } from "../api";
import type { CliMode, RunSummary } from "../types";

const runs = ref<RunSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

const load = async () => {
  loading.value = true;
  error.value = null;
  try {
    runs.value = await listRuns(5);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load runs.";
  } finally {
    loading.value = false;
  }
};

const modeLabel = (mode: CliMode) => {
  switch (mode) {
    case "run-once":
      return "Run";
    case "ai-purge":
      return "AI purge";
    case "reset-ai-purge":
      return "Reset AI purge";
    case "parse-descriptions":
      return "Parse descriptions";
    case "get-descriptions":
      return "Get descriptions";
    case "db-summary":
      return "DB summary";
    case "test":
      return "Test";
    case "purge":
      return "Purge";
    default:
      return mode;
  }
};

const modeClass = (mode: CliMode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;

const formatDate = (value?: string | null) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
};

onMounted(load);

defineExpose({ reload: load });
</script>

<style scoped>
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.list {
  display: grid;
  gap: 8px;
}

.row {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px;
  background: #fff;
}

.row p {
  margin: 0;
}

.top {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 4px;
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

.pill.mode-ai-purge {
  background: #e0f2fe;
  color: #0284c7;
}

.pill.mode-reset-ai-purge {
  background: #f1f5f9;
  color: #0f172a;
}

.pill.mode-parse-descriptions {
  background: #ecfeff;
  color: #0ea5e9;
}

.pill.mode-get-descriptions {
  background: #e0f7f0;
  color: #0f766e;
}

.pill.mode-db-summary {
  background: #fff7ed;
  color: #b45309;
}

.pill.mode-test {
  background: #f3e8ff;
  color: #7c3aed;
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

.error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 10px;
  border-radius: 10px;
  font-weight: 600;
}
</style>
