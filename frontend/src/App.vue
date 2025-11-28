<template>
  <div class="page">
    <header class="hero">
      <div>
        <p class="eyebrow">Job Informer</p>
        <h1>Trigger scrapes and browse your job database.</h1>
        <p class="subhead">Minimal Vue UI that talks to the FastAPI layer you started on port 8000.</p>
      </div>
      <div class="badge">
        <span class="dot" />
        API {{ apiBase }}
      </div>
    </header>

    <section class="panel stats" v-if="stats">
      <div class="stat">
        <div class="label">Total jobs</div>
        <div class="value">{{ stats.total_jobs.toLocaleString() }}</div>
      </div>
      <div class="stat">
        <div class="label">Last 7 days</div>
        <div class="value">{{ stats.recent_jobs_7_days.toLocaleString() }}</div>
      </div>
      <div class="stat">
        <div class="label">Date range</div>
        <div class="value">
          <span v-if="stats.date_range.earliest">
            {{ shortDate(stats.date_range.earliest) }} – {{ shortDate(stats.date_range.latest || "") }}
          </span>
          <span v-else>n/a</span>
        </div>
      </div>
    </section>

    <main class="layout">
      <div class="column">
        <RunPane />
        <JobTable class="panel" @select="onSelectJob" />
      </div>
      <JobDetail class="panel detail" :job="selectedJob" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import RunPane from "./components/RunPane.vue";
import JobTable from "./components/JobTable.vue";
import JobDetail from "./components/JobDetail.vue";
import { fetchStats } from "./api";
import type { Job, JobStats } from "./types";

const stats = ref<JobStats | null>(null);
const selectedJob = ref<Job | null>(null);
const apiBase = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const onSelectJob = (job: Job | null) => {
  selectedJob.value = job;
};

const shortDate = (value: string) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
};

onMounted(async () => {
  try {
    stats.value = await fetchStats();
  } catch (err) {
    console.warn("Could not load stats", err);
  }
});
</script>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 18px 64px;
}

.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;
}

.eyebrow {
  font-size: 14px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 4px;
}

h1 {
  margin: 0 0 8px;
  font-size: 28px;
}

.subhead {
  margin: 0;
  color: var(--muted);
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 10px 14px;
  font-weight: 600;
  box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 6px rgba(34, 197, 94, 0.18);
}

.panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px;
  box-shadow: 0 16px 45px rgba(15, 23, 42, 0.06);
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.stat {
  padding: 10px 12px;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  border: 1px dashed var(--border);
}

.label {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 2px;
}

.value {
  font-weight: 700;
  font-size: 18px;
}

.layout {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 16px;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail {
  min-height: 380px;
}

@media (max-width: 960px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .detail {
    order: 3;
  }

  .hero {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
