<template>
  <div class="page">
    <header class="hero">
      <div class="hero-left">
        <h1>Job Informer</h1>
        <div class="hero-meta">
          <span class="eyebrow">{{ formattedNow }}</span>
          <p class="muted small">Scrape, parse, and keep an eye on the database footprint.</p>
        </div>
      </div>
      <div class="hero-actions">
        <div class="badge">
          <span class="dot" />
          API {{ apiBase }}
        </div>
        <div class="view-toggle">
          <button type="button" :class="{ active: viewMode === 'dashboard' }" @click="viewMode = 'dashboard'">
            Dashboard
          </button>
          <button type="button" :class="{ active: viewMode === 'summary' }" @click="viewMode = 'summary'">
            Summary
          </button>
        </div>
      </div>
    </header>

    <main v-if="viewMode === 'dashboard'" class="layout">
      <div class="main">
        <RunPane class="highlight" />
        <div class="jobs">
          <JobTable
            ref="tableRef"
            @select="onSelectJob"
            :sources="stats?.sources_list || []"
            :companies="stats?.companies_list || []"
          />
          <JobDetail :job="selectedJob" @deleted="onDeleted" />
        </div>
      </div>
    </main>

    <SummaryPage v-else class="summary-shell" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import RunPane from "./components/RunPane.vue";
import JobTable from "./components/JobTable.vue";
import JobDetail from "./components/JobDetail.vue";
import SummaryPage from "./components/SummaryPage.vue";
import { fetchStats } from "./api";
import type { Job, JobStats } from "./types";

const stats = ref<JobStats | null>(null);
const statsError = ref<string | null>(null);
const selectedJob = ref<Job | null>(null);
const apiBase = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const viewMode = ref<"dashboard" | "summary">("dashboard");
const now = ref(new Date());
const tableRef = ref<InstanceType<typeof JobTable> | null>(null);
let clock: number | null = null;

const onSelectJob = (job: Job | null) => {
  selectedJob.value = job;
};

const shortDate = (value: string) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
};

const formattedNow = computed(() =>
  new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(now.value)
);

onMounted(async () => {
  try {
    stats.value = await fetchStats();
  } catch (err) {
    statsError.value = "Could not load stats (check API is up and token matches).";
  }
  clock = window.setInterval(() => (now.value = new Date()), 30000);
});

onBeforeUnmount(() => {
  if (clock) window.clearInterval(clock);
});

const onDeleted = () => {
  selectedJob.value = null;
  tableRef.value?.reload?.(true);
};
</script>

<style scoped>
.page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 20px 16px 56px;
}

.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 6px 0;
}

h1 {
  margin: 0;
  font-size: 20px;
}

.hero-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.hero-meta {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 6px 10px;
  font-weight: 600;
  box-shadow: 0 6px 24px rgba(15, 23, 42, 0.08);
  font-size: 12px;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.view-toggle {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
}

.view-toggle button {
  border: none;
  background: transparent;
  padding: 9px 14px;
  font-weight: 700;
  color: var(--muted);
}

.view-toggle button.active {
  background: linear-gradient(90deg, #0ea5e9 0%, #2563eb 100%);
  color: #fff;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 5px rgba(34, 197, 94, 0.18);
}

.stats {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.stat {
  padding: 8px 10px;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  border: 1px dashed var(--border);
}

.stat .label {
  margin-bottom: 2px;
  display: inline-block;
}

.warning {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #facc15;
}

.value {
  font-weight: 700;
  font-size: 16px;
}

.layout {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
  align-items: start;
}

.main {
  display: grid;
  gap: 12px;
}

.jobs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: start;
}

@media (max-width: 1100px) {
  .jobs {
    grid-template-columns: 1fr;
  }
}
</style>
