<template>
  <section>
    <div class="table-header">
      <div>
        <p class="label">Jobs</p>
        <h3>{{ total ? total.toLocaleString() : "No" }} records</h3>
      </div>
      <div class="controls">
        <input v-model="search" class="input" placeholder="Search title or company" @keyup.enter="load" />
        <select v-model="source" class="input" @change="load">
          <option value="">Any source</option>
          <option value="indeed">Indeed</option>
          <option value="linkedin">LinkedIn</option>
          <option value="stepstone">StepStone</option>
        </select>
        <button class="ghost" type="button" @click="load" :disabled="loading">Refresh</button>
      </div>
    </div>

    <div class="table-card">
      <div v-if="error" class="error">{{ error }}</div>
      <div v-else>
        <div v-if="loading" class="empty">Loading jobs…</div>
        <div v-else-if="jobs.length === 0" class="empty">No jobs found for this filter.</div>
        <div v-else class="list">
          <article
            v-for="job in jobs"
            :key="job.job_id"
            class="row"
            @click="selectJob(job)"
            :class="{ active: job.job_id === selectedId }"
          >
            <div class="title">{{ job.title }}</div>
            <div class="meta">
              <span>{{ job.company }}</span>
              <span>•</span>
              <span>{{ job.location }}</span>
            </div>
            <div class="tags">
              <span class="tag">{{ job.source }}</span>
              <span v-if="job.salary" class="tag soft">{{ job.salary }}</span>
              <span class="tag soft">{{ formatDate(job.scraped_at) }}</span>
            </div>
          </article>
        </div>
      </div>
    </div>

    <div class="pagination">
      <button class="ghost" type="button" :disabled="offset === 0 || loading" @click="prevPage">Prev</button>
      <span class="label">Page {{ pageNumber }}</span>
      <button class="ghost" type="button" :disabled="!hasNext || loading" @click="nextPage">Next</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fetchJobs } from "../api";
import type { Job } from "../types";

const emit = defineEmits<{
  (e: "select", job: Job | null): void;
}>();

const jobs = ref<Job[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = 20;
const loading = ref(false);
const error = ref<string | null>(null);
const search = ref("");
const source = ref("");
const selectedId = ref<string | null>(null);

const pageNumber = computed(() => Math.floor(offset.value / limit) + 1);
const hasNext = computed(() => offset.value + limit < total.value);

const load = async () => {
  loading.value = true;
  error.value = null;
  try {
    const data = await fetchJobs({
      limit,
      offset: offset.value,
      search: search.value,
      source: source.value,
    });
    jobs.value = data.items;
    total.value = data.total;
    if (jobs.value.length && !selectedId.value) {
      selectJob(jobs.value[0]);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load jobs.";
  } finally {
    loading.value = false;
  }
};

const selectJob = (job: Job | null) => {
  selectedId.value = job?.job_id || null;
  emit("select", job);
};

const nextPage = () => {
  if (!hasNext.value) return;
  offset.value += limit;
  load();
};

const prevPage = () => {
  offset.value = Math.max(0, offset.value - limit);
  load();
};

const formatDate = (value?: string) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
};

onMounted(load);
</script>

<style scoped>
.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.input {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: #f8fafc;
  min-width: 170px;
}

.table-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  background: var(--card);
}

.list {
  display: grid;
}

.row {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.15s ease, transform 0.1s ease;
}

.row:last-child {
  border-bottom: none;
}

.row:hover {
  background: #f8fafc;
  transform: translateY(-1px);
}

.row.active {
  background: #e0f2fe;
}

.title {
  font-weight: 700;
  margin-bottom: 4px;
}

.meta {
  display: flex;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

.tags {
  margin-top: 6px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  background: #0ea5e9;
  color: #fff;
  font-size: 12px;
  text-transform: capitalize;
}

.tag.soft {
  background: #f1f5f9;
  color: #0f172a;
}

.empty {
  padding: 20px;
  text-align: center;
  color: var(--muted);
}

.error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 12px;
  border-radius: 10px;
  margin: 8px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

.ghost {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
}

.label {
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0;
}

h3 {
  margin: 2px 0 0;
}
</style>
