import React, { useEffect, useState } from "react";
import { fetchJobExtraction } from "../api";
import type { AIClaim, AIEvidence, AIJobResult } from "../types";

const label = (value: string) => value.replace(/_/g, " ");
const claimText = (claim: AIClaim) => claim.canonical_term || claim.term || claim.qualification || claim.value || claim.language || claim.wording || claim.explanation || claim.code || "";

function experienceText(item: AIClaim): string | null {
  const years = item.years;
  if (!years) return null;
  if (years.kind === "minimum") return `At least ${years.lower} years`;
  if (years.kind === "maximum") return `At most ${years.upper} years`;
  if (years.kind === "exact") return `Exactly ${years.lower} years`;
  if (years.kind === "ambiguous_minimum") return `Minimum threshold stated as ${years.lower}–${years.upper} years; ambiguous`;
  return `Target range ${years.lower}–${years.upper} years; not an eligibility ceiling`;
}

function Alternatives({ item, claims }: { item: AIClaim; claims: AIClaim[] }) {
  if (!item.alternative_group) return null;
  const peers = claims.filter((other) => other !== item && other.alternative_group === item.alternative_group);
  const together = peers.filter((other) => item.alternative_option && other.alternative_option === item.alternative_option);
  const options = new Map<string, string[]>();
  peers.filter((other) => !together.includes(other)).forEach((other, index) => {
    const key = other.alternative_option || String(index);
    options.set(key, [...(options.get(key) || []), claimText(other)]);
  });
  return <>
    {together.length > 0 && <span className="muted small">Together with: {together.map(claimText).join(" + ")}</span>}
    <span className="muted small">Alternative to: {[...options.values()].map((terms) => terms.join(" + ")).join(" OR ") || "another stated option"}</span>
  </>;
}

function Evidence({ items, sources }: { items: AIEvidence[]; sources: Record<string, string> }) {
  return <details className="extraction-evidence"><summary>Show evidence</summary>{items.map((item, i) => {
    const source = sources[item.source_ref] || item.quote;
    return <blockquote key={`${item.source_ref}-${i}`}><span className="muted tiny">{label(item.source_ref)}</span>
      <p>{source.slice(0, item.start)}<mark>{source.slice(item.start, item.end)}</mark>{source.slice(item.end)}</p></blockquote>;
  })}</details>;
}

const JobExtraction: React.FC<{ jobId: string; refreshToken?: number }> = ({ jobId, refreshToken = 0 }) => {
  const [result, setResult] = useState<AIJobResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    setResult(null);
    const load = async () => {
      try {
        const next = await fetchJobExtraction(jobId);
        if (cancelled) return;
        setResult(next);
        setError(null);
        if (next.status === "running" || (!next.queue.paused && ["queued", "retry_wait"].includes(next.status || ""))) timer = window.setTimeout(load, 2000);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load extracted information.");
      }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId, refreshToken, reload]);
  const saved = result?.saved;
  const fields = saved?.fields;
  const sources = result?.input?.sources || {};
  const groups: Array<[string, AIClaim[]]> = fields ? [
    ["requirements", fields.requirements], ...(fields.education ? [["education", fields.education] as [string, AIClaim[]]] : []),
    ["languages", fields.languages], ["experience", fields.experience],
    ["work_arrangement", fields.work_arrangement], ["responsibilities", fields.responsibilities],
    ["seniority", fields.seniority], ["employment_type", fields.employment_type],
  ] : [];
  return <section className="job-extraction" aria-label="Extracted job information">
    <div className="description-heading"><h3>Extracted information</h3>{fields && <span className="pill small language-code" title="Languages used in the description, dominant first">{fields.description_language}</span>}</div>
    {result?.legacy_review && <p className="description-notice">Saved review: {label(result.legacy_review.status)} · {label(result.legacy_review.priority)} priority</p>}
    {error && <p role="alert" className="description-error">{error} <button type="button" className="ghost sm" onClick={() => setReload((value) => value+1)}>Reload extracted information</button></p>}
    {result?.status && result.status !== "succeeded" && <p className="muted small">AI extraction: {label(result.status)}{result.queue.paused ? " · paused" : ""}</p>}
    {result?.error && <p className="description-notice">{result.error}</p>}
    {Boolean(result?.rejected) && <details className="extraction-group"><summary>Output needing review · Not accepted</summary><pre className="extraction-draft">{JSON.stringify(result!.rejected, null, 2)}</pre></details>}
    {result?.stale && <p className="description-notice">This result uses an earlier source or extraction version. Its original evidence remains available.</p>}
    {result && !saved && <p className="muted small">No new AI result yet. Use Extract AI above to analyze this job’s saved description.</p>}
    {saved && fields && <>
      <p className="muted small">Structure and source quotes checked · Not yet reviewed for accuracy</p>
      {!fields.education && <p className="muted small">This earlier extraction has no dedicated education field.</p>}
      <details className="extraction-provenance"><summary>About this extraction</summary>
        <p className="muted tiny">{new Date(saved.extracted_at).toLocaleString()} · {saved.metadata.model} · {saved.contract.reasoning} · {saved.metadata.service_tier}</p>
        <p className="muted small">Source posting: {sources.title} · {sources.location}</p>
      </details>
      {result?.input?.source_quality === "legacy_completeness_unknown" && <p className="description-notice">Based on earlier saved text; its completeness is unknown.</p>}
      {fields.description_languages.length > 0 && <details className="extraction-group"><summary>Description language · <span className="language-code">{fields.description_language}</span></summary>
        {fields.description_languages.map((item) => <div key={item.code}><strong>{item.code}</strong><Evidence items={item.evidence} sources={sources} /></div>)}
      </details>}
      {fields.conflicts.length > 0 && <div className="description-notice"><strong>Conflicting information</strong>{fields.conflicts.map((item, i) => <div key={i}><p>{label(item.field || "")}: {item.explanation}</p><Evidence items={item.evidence} sources={sources} /></div>)}</div>}
      {groups.map(([name, claims]) => <details className="extraction-group" key={name}>
        <summary>{name === "languages" ? "Candidate language requirements" : label(name)} <span className="muted small">· {claims.length || label(fields.states[name] || "not stated")}</span></summary>
        {claims.map((item, i) => <div className="extraction-claim" key={i}><strong>{claimText(item)}</strong>
          <span className="muted small">{[item.strength && label(item.strength), item.proficiency, item.cefr, item.scope,
            item.mode && label(item.mode), item.level && label(item.level), item.fields_of_study?.join(" / "),
            item.condition && `Applies: ${item.condition}`, item.office_attendance, item.geographic_restrictions, experienceText(item),
            item.minimum_years != null ? `Minimum ${item.minimum_years} years` : null,
            item.maximum_years != null ? `Maximum ${item.maximum_years} years` : null].filter(Boolean).join(" · ")}</span>
          <Alternatives item={item} claims={claims} />
          <Evidence items={item.evidence} sources={sources} />
        </div>)}
        {!claims.length && <p className="muted small">{fields.states[name] === "not_assessable" ? "The saved text is insufficient to assess this field." : "No supported information found. This does not mean there is no requirement."}</p>}
      </details>)}
    </>}
    {result?.legacy && <details className="extraction-group"><summary>Earlier AI results · Unvalidated</summary>
      <p className="muted tiny">{result.legacy.model} · Version {result.legacy.version} · {new Date(result.legacy.created_at).toLocaleString()}</p>
      <p className="muted small">These historical fields have no individual source quotations.</p>
      <dl className="legacy-fields">{Object.entries(result.legacy.fields).filter(([name]) => name !== "dry_run").map(([name, value]) => <div key={name}><dt>{label(name)}</dt><dd>{Array.isArray(value) ? value.join(", ") || "Not stated" : value === null ? "Not stated" : typeof value === "object" ? Object.entries(value).map(([key, part]) => `${key}: ${part ?? "unknown"}`).join(" · ") : String(value)}</dd></div>)}</dl>
    </details>}
  </section>;
};

export default JobExtraction;
