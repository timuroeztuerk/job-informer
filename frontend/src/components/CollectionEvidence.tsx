import React from "react";
import type { CollectionValidation } from "../types";

interface Props {
  evidence: CollectionValidation;
  onReview: (filter: "unmatched" | "auto_archived" | "flagged") => void;
}

const CollectionEvidence: React.FC<Props> = ({ evidence, onReview }) => {
  const remaining = Math.max(0, evidence.required_days - evidence.healthy_days);
  const searchWindow = ({ day: "One-day", week: "Seven-day", month: "Thirty-day" } as Record<string, string>)[evidence.baseline?.time_range || ""] || evidence.baseline?.time_range;
  return <section className="market-panel collection-evidence" aria-labelledby="evidence-title">
    <div className="market-section-heading">
      <div><p className="label">Collection validation</p><h3 id="evidence-title">Evidence across collection days</h3></div>
      <span className={`market-status ${remaining ? "caution" : "good"}`}>{evidence.healthy_days} of {evidence.required_days} days collected</span>
    </div>
    <p className="evidence-next-step">{remaining
      ? `${remaining} more separate ${remaining === 1 ? "day" : "days"} with complete, matching searches needed.`
      : "Collection-day target reached. Review filter quality and city-only roles before moving on."}</p>
    {evidence.baseline ? <>
      <p className="muted small">{searchWindow} searches · {evidence.baseline.keywords.split(",").map((value) => value.trim()).join(" + ")}<br />{evidence.baseline.locations.split(",").map((value) => value.trim()).join(" · ")}</p>
      <p className="market-footnote">One healthy run per start date in {evidence.timezone}, using the latest requested terms, locations, and search window. Separate from the market time window above. {evidence.other_runs > 0 && `${evidence.other_runs} other ${evidence.other_runs === 1 ? "run has" : "runs have"} different or incomplete search settings.`}</p>
      {evidence.cities.length > 0 && <div className="evidence-city-table">
        <table>
          <caption>What each retained city adds</caption>
          <thead><tr><th scope="col">City search</th><th scope="col">Days adding jobs</th><th scope="col">Results absent from country searches</th><th scope="col">Covered by country searches</th></tr></thead>
          <tbody>{evidence.cities.map((city) => <tr key={city.location}>
            <th scope="row">{city.location}</th><td>{city.days_adding_jobs} / {city.sampled_days}</td><td>{city.city_only_jobs.toLocaleString()}</td><td>{city.unique_jobs ? `${(city.shared_jobs / city.unique_jobs * 100).toFixed(1)}%` : "—"}</td>
          </tr>)}</tbody>
        </table>
        <p className="market-footnote">Totals across the counted daily samples, including archived jobs. Returning jobs count once per sampled day and may appear in several cities. Review their relevance before removing a city search.</p>
      </div>}
      <details className="evidence-history">
        <summary>Inspect {evidence.matching_runs} matching collection {evidence.matching_runs === 1 ? "attempt" : "attempts"}</summary>
        <table>
          <thead><tr><th scope="col">Collection day</th><th scope="col">Evidence</th><th scope="col">Pages</th><th scope="col">City-only results</th><th scope="col">City results covered</th></tr></thead>
          <tbody>{evidence.runs.map((run) => <tr key={run.run_id}>
            <th scope="row"><time dateTime={run.started_at}>{run.date}</time></th>
            <td><span className={`evidence-run-state ${run.healthy ? "" : "needs-review"}`}>{run.counted ? "Counted day" : run.healthy ? "Same day · not counted again" : "Needs attention"}</span>{run.concerns.length > 0 && <ul>{run.concerns.map((concern) => <li key={concern}>{concern}</li>)}</ul>}</td>
            <td>{run.pages_completed}/{run.pages_attempted}</td><td>{run.city_only_jobs?.toLocaleString() ?? "—"}</td><td>{run.city_coverage_percent == null ? "—" : `${run.city_coverage_percent}%`}</td>
          </tr>)}</tbody>
        </table>
      </details>
    </> : <p className="muted">No comparable country-and-city run is recorded yet. Collect with explicit search terms, both countries, the four retained cities, and a search window to start this check.</p>}
    <div className="evidence-review-actions"><span>Continue the manual filter audit</span>
      <button type="button" className="market-text-link" onClick={() => onReview("unmatched")}>Review unmatched jobs ↗</button>
      <button type="button" className="market-text-link" onClick={() => onReview("auto_archived")}>Check automatic archives ↗</button>
      <button type="button" className="market-text-link" onClick={() => onReview("flagged")}>Review flagged examples ↗</button>
    </div>
    <p className="market-footnote">These checks measure recorded collection coverage. Filter quality still needs manual review; flags are examples for that review.</p>
  </section>;
};

export default CollectionEvidence;
