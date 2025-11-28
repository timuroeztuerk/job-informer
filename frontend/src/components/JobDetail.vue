<template>
  <div>
    <p class="label">Details</p>
    <div v-if="!job">Select a job to see details.</div>

    <div v-else class="card">
      <header>
        <div>
          <p class="company">{{ job.company }}</p>
          <h3>{{ job.title }}</h3>
          <p class="muted">{{ job.location }} • {{ job.source }}</p>
        </div>
        <a v-if="job.url" class="link" :href="job.url" target="_blank" rel="noreferrer">Open posting</a>
      </header>

      <div v-if="error" class="error">{{ error }}</div>
      <div v-else-if="loading" class="muted">Loading description…</div>
      <div v-else-if="details">
        <p class="muted">Scraped {{ formatDate(details.scraped_at) }}</p>
        <p class="body" v-if="details.description">{{ details.description }}</p>
        <p v-else class="muted">No description stored.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { fetchJob } from "../api";
import type { Job } from "../types";

const props = defineProps<{
  job: Job | null;
}>();

const loading = ref(false);
const error = ref<string | null>(null);
const details = ref<Job | null>(null);

const load = async () => {
  if (!props.job) {
    details.value = null;
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    details.value = await fetchJob(props.job.job_id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load job.";
  } finally {
    loading.value = false;
  }
};

const formatDate = (value?: string) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
};

watch(
  () => props.job?.job_id,
  () => load(),
  { immediate: true }
);

onMounted(load);
</script>

<style scoped>
.card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px;
  background: #f8fafc;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.company {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 12px;
  margin: 0 0 2px;
  color: var(--muted);
}

h3 {
  margin: 0 0 2px;
}

.muted {
  color: var(--muted);
  margin: 0;
}

.body {
  white-space: pre-wrap;
  line-height: 1.6;
}

.link {
  color: #0ea5e9;
  font-weight: 700;
}

.label {
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 6px;
}

.error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 10px 12px;
  border-radius: 10px;
}

@media (max-width: 960px) {
  header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
