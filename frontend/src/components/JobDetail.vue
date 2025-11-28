<template>
  <div class="panel detail-pane">
    <p class="label">Details</p>
    <div v-if="!job">Select a job to see details.</div>

    <div v-else class="card">
      <header>
        <div>
          <p class="company">{{ job.company }}</p>
          <h3>{{ job.title }}</h3>
          <p class="muted">{{ job.location }} • {{ job.source }}</p>
        </div>
        <div class="header-actions">
          <a v-if="job.url" class="link" :href="job.url" target="_blank" rel="noreferrer">Open posting</a>
          <button class="danger" type="button" @click="confirmDelete" :disabled="deleting">Delete</button>
        </div>
      </header>

      <div v-if="error" class="error">{{ error }}</div>
      <div v-else-if="loading" class="muted">Loading details…</div>
      <div v-else-if="details">
        <p class="muted">Scraped {{ formatDate(details.scraped_at) }}</p>

        <div v-if="parsed" class="parsed">
          <div class="pill-row">
            <span class="pill">{{ parsed.seniority || "unspecified" }}</span>
            <span class="pill">{{ parsed.employment_type || "unspecified" }}</span>
            <span class="pill">{{ parsed.remote || "unspecified" }}</span>
            <span class="pill">{{ parsed.location?.[0] || job.location }}</span>
          </div>

          <div class="summary">
            <p class="summary-title">Summary</p>
            <p class="body" v-if="parsed.summary">{{ parsed.summary }}</p>
            <p class="muted" v-else>No parsed summary yet.</p>
          </div>

          <div class="grid">
            <div>
              <p class="summary-title">Languages</p>
              <div class="chip-row">
                <span v-for="lang in parsed.languages || []" :key="lang" class="chip">{{ lang }}</span>
                <span v-if="!parsed.languages || !parsed.languages.length" class="muted small">n/a</span>
              </div>
            </div>
            <div>
              <p class="summary-title">Programming</p>
              <div class="chip-row">
                <span v-for="lang in parsed.programming_languages || []" :key="lang" class="chip">{{ lang }}</span>
                <span v-if="!parsed.programming_languages || !parsed.programming_languages.length" class="muted small">n/a</span>
              </div>
            </div>
            <div>
              <p class="summary-title">Tools</p>
              <div class="chip-row">
                <span v-for="tool in parsed.tools || []" :key="tool" class="chip">{{ tool }}</span>
                <span v-if="!parsed.tools || !parsed.tools.length" class="muted small">n/a</span>
              </div>
            </div>
            <div>
              <p class="summary-title">Skills</p>
              <div class="chip-row">
                <span v-for="skill in parsed.skills || []" :key="skill" class="chip">{{ skill }}</span>
                <span v-if="!parsed.skills || !parsed.skills.length" class="muted small">n/a</span>
              </div>
            </div>
          </div>

          <div class="meta">
            <div>
              <p class="summary-title">Degree</p>
              <p class="muted">{{ parsed.degree_field || "unspecified" }} • {{ parsed.degree_type || "unspecified" }}</p>
            </div>
            <div>
              <p class="summary-title">Experience</p>
              <p class="muted">{{ parsed.years_experience_min ?? "unspecified" }}+ years</p>
            </div>
            <div>
              <p class="summary-title">Salary (EUR)</p>
              <p class="muted">{{ salaryText(parsed.salary_eur_range) }}</p>
            </div>
            <div>
              <p class="summary-title">Benefits</p>
              <p class="muted">{{ benefitsText(parsed["extra benefits"]) }}</p>
            </div>
          </div>
        </div>

        <details class="raw" v-if="details.description">
          <summary>Full description</summary>
          <p class="body">{{ details.description }}</p>
        </details>
        <p v-else class="muted">No description stored.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { deleteJob, fetchJob } from "../api";
import type { Job, ParsedPayload } from "../types";

const props = defineProps<{
  job: Job | null;
}>();

const emit = defineEmits<{ (e: "deleted"): void }>();

const loading = ref(false);
const error = ref<string | null>(null);
const details = ref<Job | null>(null);
const parsed = ref<ParsedPayload | null>(null);
const deleting = ref(false);

const load = async () => {
  if (!props.job) {
    details.value = null;
    parsed.value = null;
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    details.value = await fetchJob(props.job.job_id);
    parsed.value = (details.value?.parsed_description?.payload as ParsedPayload) || null;
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

const confirmDelete = async () => {
  if (!details.value || deleting.value) return;
  const ok = window.confirm("Delete this job? This cannot be undone.");
  if (!ok) return;
  deleting.value = true;
  try {
    await deleteJob(details.value.job_id);
    details.value = null;
    parsed.value = null;
    emit("deleted");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to delete job.";
  } finally {
    deleting.value = false;
  }
};

const salaryText = (range?: { min?: number | null; max?: number | null }) => {
  if (!range) return "unspecified";
  const { min, max } = range;
  if (min && max) return `${min.toLocaleString()} – ${max.toLocaleString()}`;
  if (min) return `${min.toLocaleString()}+`;
  if (max) return `up to ${max.toLocaleString()}`;
  return "unspecified";
};

const benefitsText = (benefits?: string | string[]) => {
  if (!benefits) return "unspecified";
  if (Array.isArray(benefits)) return benefits.length ? benefits.join(", ") : "unspecified";
  return benefits;
};

watch(
  () => props.job?.job_id,
  () => load(),
  { immediate: true }
);

onMounted(load);
</script>

<style scoped>
.detail-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 420px;
}

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

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
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

.detail-pane .muted {
  margin: 0;
}

.body {
  white-space: pre-wrap;
  line-height: 1.6;
}

.parsed {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 10px 0;
}

.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pill {
  padding: 6px 10px;
  border-radius: 999px;
  background: #e2e8f0;
  font-weight: 700;
  text-transform: capitalize;
}

.summary {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px 12px;
}

.summary-title {
  margin: 0 0 4px;
  font-weight: 700;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  background: #0f172a;
  color: #e2e8f0;
  padding: 4px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.raw {
  border: 1px dashed var(--border);
  border-radius: 12px;
  padding: 8px 10px;
  background: #fff;
}

.link {
  color: #0ea5e9;
  font-weight: 700;
}

.danger {
  background: #fee2e2;
  color: #b91c1c;
  border: 1px solid #fecaca;
  border-radius: 10px;
  padding: 8px 10px;
  font-weight: 700;
  cursor: pointer;
}

.detail-pane .label {
  margin: 0 0 6px;
  display: inline-block;
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
