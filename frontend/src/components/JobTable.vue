<template>
  <section class="panel job-table">
    <div class="table-header">
      <div>
        <p class="label">Jobs</p>
        <h3>{{ total ? total.toLocaleString() : "No" }} records</h3>
      </div>
      <div class="controls">
        <input v-model="search" class="input" placeholder="Search title or company" @keyup.enter="load(true)" />
        <select v-model="source" class="input" @change="load(true)">
          <option value="">Any source</option>
          <option v-for="s in sourceOptions" :key="s" :value="s">{{ s }}</option>
        </select>
        <select v-model="company" class="input" @change="load(true)">
          <option value="">Any company</option>
          <option v-for="c in companyOptions" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>
    </div>

    <div class="filters inline">
      <label class="tiny">
        <span>Date from</span>
        <input v-model="dateFrom" type="date" class="input" @change="load(true)" />
      </label>
      <label class="tiny">
        <span>Date to</span>
        <input v-model="dateTo" type="date" class="input" @change="load(true)" />
      </label>
      <label class="tiny">
        <span>Sort</span>
        <select v-model="sort" class="input" @change="load(true)">
          <option value="scraped_at_desc">Newest</option>
          <option value="scraped_at_asc">Oldest</option>
          <option value="title_asc">Title A-Z</option>
          <option value="title_desc">Title Z-A</option>
        </select>
      </label>
      <button class="ghost sm" type="button" @click="load(true)" :disabled="loading">Refresh</button>
      <button class="ghost sm" type="button" @click="clearFilters" :disabled="loading">Clear</button>
    </div>

    <div class="table-card">
      <div v-if="error" class="error">
        {{ error }}
        <button class="ghost sm" type="button" @click="load()" :disabled="loading">Retry</button>
      </div>
      <div v-else>
        <div v-if="loading" class="skeletons">
          <div v-for="i in 5" :key="i" class="skeleton-row"></div>
        </div>
        <div v-else-if="jobs.length === 0" class="empty">No jobs found. Try loosening filters.</div>
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

const props = defineProps<{
  sources: string[];
  companies: string[];
}>();

const sourceOptions = computed(() => props.sources || []);
const companyOptions = computed(() => props.companies || []);

const jobs = ref<Job[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = 5;
const loading = ref(false);
const error = ref<string | null>(null);
const search = ref("");
const source = ref("");
const company = ref("");
const dateFrom = ref("");
const dateTo = ref("");
const sort = ref<"scraped_at_desc" | "scraped_at_asc" | "title_asc" | "title_desc">("scraped_at_desc");
const selectedId = ref<string | null>(null);

const pageNumber = computed(() => Math.floor(offset.value / limit) + 1);
const hasNext = computed(() => offset.value + limit < total.value);

const load = async (reset = false) => {
  if (reset) {
    offset.value = 0;
  }
  loading.value = true;
  error.value = null;
  try {
    const data = await fetchJobs({
      limit,
      offset: offset.value,
      search: search.value,
      source: source.value,
      company: company.value,
      dateFrom: dateFrom.value,
      dateTo: dateTo.value,
      sort: sort.value,
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

const clearFilters = () => {
  search.value = "";
  source.value = "";
  company.value = "";
  dateFrom.value = "";
  dateTo.value = "";
  sort.value = "scraped_at_desc";
  load(true);
};

onMounted(load);

defineExpose({
  reload: (reset = true) => load(reset),
});
</script>

<style scoped>
.job-table {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.table-header .label {
  margin-bottom: 2px;
  display: inline-block;
}

.controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.input {
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: #f8fafc;
  min-width: 140px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: nowrap;
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: none;
  white-space: nowrap;
}

.filters::-webkit-scrollbar {
  display: none;
}

.filters > * {
  flex: 0 0 auto;
}

.filters.inline .input {
  width: 120px;
  min-width: 110px;
  padding: 6px 8px;
  height: 32px;
}

.tiny {
  display: inline-flex;
  flex-direction: column;
  gap: 2px;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.table-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  background: var(--card);
  max-height: 420px;
  overflow-y: auto;
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

.error .sm {
  margin-left: 8px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

h3 {
  margin: 2px 0 0;
}

.skeletons {
  display: grid;
  gap: 6px;
  padding: 12px;
}

.skeleton-row {
  height: 64px;
  border-radius: 10px;
  background: linear-gradient(90deg, #eef2f7 25%, #f5f7fb 50%, #eef2f7 75%);
  background-size: 400% 100%;
  animation: shimmer 1.1s ease-in-out infinite;
}

@keyframes shimmer {
  0% {
    background-position: 100% 0;
  }
  100% {
    background-position: -100% 0;
  }
}
</style>
