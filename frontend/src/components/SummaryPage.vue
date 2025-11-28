<template>
  <div class="summary-page">
    <section class="panel intro">
      <div class="intro-row">
        <div>
          <p class="label">Summary</p>
          <h2>Database snapshot</h2>
          <p class="muted">
            Review parsed description trends, coverage, and city distribution without touching the CLI.
          </p>
        </div>
        <div class="intro-actions">
          <span v-if="parsed" class="pill small tone">Parsed payloads: {{ parsed.total_records.toLocaleString() }}</span>
          <button class="ghost" type="button" @click="reload" :disabled="loading">Refresh</button>
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>
      <div v-else-if="loading" class="muted">Loading summary...</div>
      <div v-else-if="summary" class="stat-grid">
        <div class="stat-card">
          <p class="label">Total jobs</p>
          <p class="stat-value">{{ summary.totals.total_jobs.toLocaleString() }}</p>
          <p class="muted tiny">All rows currently stored.</p>
        </div>
        <div class="stat-card">
          <p class="label">Last 7 days</p>
          <p class="stat-value">{{ summary.totals.recent_jobs_7_days.toLocaleString() }}</p>
          <p class="muted tiny">Fresh additions in the last week.</p>
        </div>
        <div class="stat-card">
          <p class="label">Date range</p>
          <p class="stat-value small">
            <span v-if="summary.totals.date_range.earliest">
              {{ shortDate(summary.totals.date_range.earliest) }} - {{ shortDate(summary.totals.date_range.latest) }}
            </span>
            <span v-else>n/a</span>
          </p>
          <p class="muted tiny">Earliest and latest scrape dates.</p>
        </div>
        <div class="stat-card" v-if="summary.parsed_descriptions_stats">
          <p class="label">Parsed coverage</p>
          <p class="stat-value">
            {{ (summary.parsed_descriptions_stats.jobs_with_descriptions || parsed?.total_records || 0).toLocaleString() }}
          </p>
          <p class="muted tiny">
            Parsed jobs: {{ parsed?.total_records.toLocaleString() || "0" }} | Orphans:
            {{ (summary.parsed_descriptions_stats.orphaned_parsed_descriptions || 0).toLocaleString() }}
          </p>
        </div>
      </div>
    </section>

    <section v-if="parsed && parsed.total_records" class="panel insight-panel">
      <div class="insight-grid">
        <div class="insight">
          <div class="insight-head">
            <p class="label">Programming languages</p>
            <p class="muted tiny">Share of parsed payloads.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.programming_languages, 6)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
        <div class="insight">
          <div class="insight-head">
            <p class="label">Skills</p>
            <p class="muted tiny">Most common skills in parsed text.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.skills, 6)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
        <div class="insight">
          <div class="insight-head">
            <p class="label">Tools</p>
            <p class="muted tiny">Libraries, databases, and platforms.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.tools, 8)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
        <div class="insight">
          <div class="insight-head">
            <p class="label">Degree fields</p>
            <p class="muted tiny">Normalized fields requested.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.degree_fields, 6)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
        <div class="insight">
          <div class="insight-head">
            <p class="label">Seniority</p>
            <p class="muted tiny">Level mix across postings.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.seniority_levels, 4)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
        <div class="insight">
          <div class="insight-head">
            <p class="label">Employment type</p>
            <p class="muted tiny">Contract mix.</p>
          </div>
          <ul>
            <li v-for="item in top(parsed.employment_types, 4)" :key="item.name">
              <span class="name">{{ item.name }}</span>
              <span class="value">
                {{ item.count.toLocaleString() }}
                <span class="muted tiny">({{ pct(item) }})</span>
              </span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <section v-else-if="summary && !loading" class="panel note">
      <p class="muted">No parsed descriptions yet. Run the description parser to populate this view.</p>
    </section>

    <section v-if="parsed && parsed.total_records" class="panel small-grid">
      <div class="stat-card tight">
        <p class="label">Experience requirements</p>
        <p class="stat-value">
          <span v-if="parsed.experience_years.count">
            {{ parsed.experience_years.average?.toFixed(1) || "0.0" }} years avg
          </span>
          <span v-else>n/a</span>
        </p>
        <p class="muted tiny" v-if="parsed.experience_years.count">
          Range: {{ parsed.experience_years.min }} - {{ parsed.experience_years.max }} ·
          {{ parsed.experience_years.count.toLocaleString() }} jobs
        </p>
        <p class="muted tiny" v-else>Waiting for parsed experience fields.</p>
      </div>
      <div class="stat-card tight">
        <p class="label">Salary (EUR)</p>
        <p class="stat-value">
          <span v-if="parsed.salary_eur.count">€{{ round(parsed.salary_eur.average) }}</span>
          <span v-else>n/a</span>
        </p>
        <p class="muted tiny" v-if="parsed.salary_eur.count">
          Range: €{{ round(parsed.salary_eur.min) }} - €{{ round(parsed.salary_eur.max) }} ·
          {{ parsed.salary_eur.count.toLocaleString() }} jobs
        </p>
        <p class="muted tiny" v-else>Waiting for parsed salary ranges.</p>
      </div>
    </section>

    <section v-if="citySummary" class="panel city-panel">
      <div class="intro-row">
        <div>
          <p class="label">Cities</p>
          <h3>Top locations</h3>
        </div>
        <span class="pill small tone">Locations scanned: {{ citySummary.total_jobs.toLocaleString() }}</span>
      </div>
      <div v-if="citySummary.top_cities.length" class="city-list">
        <div v-for="(city, idx) in citySummary.top_cities" :key="city.name" class="city-row">
          <div class="city-rank">{{ idx + 1 }}</div>
          <div class="city-body">
            <div class="city-name">{{ city.name }}</div>
            <div class="muted tiny">
              {{ city.count.toLocaleString() }} jobs · {{ pct(city) }}
            </div>
            <div class="bar">
              <span :style="{ width: barWidth(city) }" />
            </div>
          </div>
        </div>
      </div>
      <p v-else class="muted">No jobs to summarize yet.</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fetchDbSummary } from "../api";
import type { CountStat, DbSummary } from "../types";

const summary = ref<DbSummary | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const parsed = computed(() => summary.value?.parsed_insights || null);
const citySummary = computed(() => summary.value?.city_summary || null);

const shortDate = (value?: string | null) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
};

const pct = (entry: CountStat) => `${(entry.percentage ?? 0).toFixed(1)}%`;
const top = (entries: CountStat[] = [], take = 5) => entries.slice(0, take);
const barWidth = (entry: CountStat) => `${Math.max(6, Math.min(100, entry.percentage ?? 0))}%`;
const round = (value: number | null) => (value === null || value === undefined ? "0" : Math.round(value).toLocaleString());

const reload = async () => {
  loading.value = true;
  error.value = null;
  try {
    summary.value = await fetchDbSummary();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load summary.";
  } finally {
    loading.value = false;
  }
};

onMounted(reload);

defineExpose({ reload });
</script>

<style scoped>
.summary-page {
  display: grid;
  gap: 14px;
}

.intro-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.intro-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stat-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.stat-card {
  border: 1px dashed var(--border);
  border-radius: var(--radius-soft);
  padding: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
}

.stat-card.tight {
  background: #0f172a;
  color: #e2e8f0;
  border: none;
}

.stat-value {
  margin: 4px 0;
  font-size: 22px;
  font-weight: 800;
}

.stat-value.small {
  font-size: 16px;
}

.insight-panel {
  border: 1px solid #dbeafe;
  box-shadow: 0 16px 40px rgba(14, 165, 233, 0.12);
}

.insight-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.insight {
  border: 1px solid var(--border);
  border-radius: var(--radius-soft);
  padding: 10px 12px;
  background: #fff;
}

.insight-head {
  margin-bottom: 4px;
}

ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 6px;
}

li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.name {
  font-weight: 700;
}

.value {
  font-weight: 600;
}

.note {
  background: #fffbeb;
  border: 1px solid #facc15;
}

.small-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
}

.city-panel .city-list {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}

.city-row {
  display: grid;
  grid-template-columns: 44px 1fr;
  gap: 8px;
  align-items: center;
}

.city-rank {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: #0ea5e9;
  color: #fff;
  font-weight: 800;
  box-shadow: 0 10px 24px rgba(14, 165, 233, 0.28);
}

.city-body {
  border: 1px solid var(--border);
  border-radius: var(--radius-soft);
  padding: 10px 12px;
  background: #fff;
}

.city-name {
  font-weight: 700;
}

.bar {
  margin-top: 6px;
  width: 100%;
  height: 6px;
  background: #f1f5f9;
  border-radius: 999px;
}

.bar span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #0ea5e9 0%, #2563eb 100%);
}

.tone {
  background: #e0f2fe;
  color: #0f172a;
  border: 1px solid #bae6fd;
}

.error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 10px;
  border-radius: 10px;
  font-weight: 600;
}

@media (max-width: 800px) {
  .intro-row {
    align-items: flex-start;
  }
}
</style>
