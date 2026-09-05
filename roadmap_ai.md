# AI extraction roadmap

Turn saved job postings into useful, traceable information for review and Intelligence. The extraction pipeline is implemented with a temporary 10-job pilot. Product priorities remain in [ROADMAP.md](ROADMAP.md).

## Agreed processing choices

| Choice | Contract |
| --- | --- |
| Model | `gpt-5.4-mini`; record both the requested model and the model returned by the API. |
| Reasoning | `medium`; no model or reasoning-effort comparison. |
| API | Responses API with the official async Python client and Pydantic Structured Outputs. |
| Output | Omit `max_output_tokens`; no application-set output token cap. Complete the schema and evidence without padding. The model still has a maximum output of 128,000 tokens. |
| Concurrency | Target 100 simultaneous AI requests, with a global ceiling of 100. Pace admissions against account request/token limits and back off when capacity is unavailable. |
| Processing tier | Explicit `service_tier="flex"`. Keep retries on Flex; do not silently switch to standard processing. |
| Execution | A manually started, persistent local queue. Async network calls run independently of browser requests. LinkedIn retrieval keeps its existing separate pacing. |
| Structure | Pydantic is the canonical schema; generate the API JSON schema from it and validate results locally. |

GPT-5.4 mini supports Responses and Structured Outputs. Flex is listed for the model; account access and usable throughput still need a live integration check. See the [model specification](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [Flex pricing](https://developers.openai.com/api/docs/pricing?latest-pricing=flex), and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

The removable trial block is at the beginning of [store.py](backend/src/ai/store.py):

```python
# BEGIN TEMPORARY PILOT — delete this block after reviewing the first 10 jobs.
PILOT_LIMIT = 10
# END TEMPORARY PILOT
```

The same first 10 eligible jobs remain selected across queue actions and restarts. Favorites come first, followed by latest sighting and job ID. Removing the block enables the normal queue; it does not start an archive-wide run automatically.

The first live run completed all 10 jobs, with one retry after an evidence-check failure. The results are available in Jobs for review; the temporary limit remains enabled.

Requests use medium reasoning and a 15-minute timeout. The queue owns retries (`max_retries=0` on the SDK client). Flex can be slow or return temporary capacity errors; see [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing).

## 1. Preserve supplied facts and source evidence

The current public-page extractor already saves these criteria. Keep their original labels, values, source ID, and retrieval date; normalize them deterministically alongside the originals. They must remain visible even when no AI extraction exists.

| LinkedIn field | Example | Treatment |
| --- | --- | --- |
| Seniority level | Mid-Senior level | Preserve the broad category; do not turn it into an exact experience requirement or force it into only `mid` or `senior`. |
| Employment type | Full-time | Map known labels to a canonical value while preserving the original. Full-time does not establish whether the contract is permanent. |
| Job function | Finance and Sales | Keep LinkedIn's classification separate from the role's actual responsibilities. It is not sufficient grounds for exclusion. |
| Industries | Financial Services | Keep the published industry label; do not infer the candidate's skills or experience from it. |

Build each AI input from one identified posting: title, company, location, listed criteria, and the complete saved description. Give metadata fields and description paragraphs stable source references. Preserve paragraph boundaries, qualifications, negation, and the original language. Remove page navigation without silently cutting descriptions at a character limit. Oversized or partial sources get an explicit state instead of a fabricated complete result.

Supply listed criteria as context, but copy their canonical values in application code. AI-derived claims live separately. When a description and a listed criterion disagree, retain both with their evidence and mark the field as conflicting. Neither source is guaranteed to be correct.

Each extracted claim needs a source reference and a verbatim quotation. Application code resolves the reference to the immutable input, verifies the quotation, and computes its text offsets. Matching a quotation proves it exists; output review must also check that it supports the claim. Do not use model-generated confidence percentages as a substitute.

## 2. Pydantic field design

Start with explicit nested models: `Evidence`, `Requirement`, `LanguageRequirement`, `ExperienceRequirement`, `WorkArrangement`, `Conflict`, and `JobExtraction`. Avoid a generic dictionary of arbitrary AI attributes. Use `extra="forbid"`, constrained categories, required keys, and nullable values. Enforce semantic checks after parsing as well as structural validation.

| Field group | Useful structure |
| --- | --- |
| Skills, tools, programming languages | Original term, canonical term, category, requirement strength, evidence. Preserve alternatives such as “Python or R” rather than making both mandatory. |
| Description language | Any language, with ISO codes ordered by prominence: `de/en`, `en`, `fr`, etc. Use description passages, excluding metadata labels and isolated technical terms. |
| Applicant languages | Language, stated proficiency wording, explicit CEFR level if supplied, requirement strength, evidence. Do not convert “fluent” into an invented CEFR level. |
| Experience | Minimum/maximum years where explicit, relevant discipline or technology, requirement strength, evidence. Keep “several years” as wording with unknown numeric bounds. |
| Work arrangement | Remote/on-site/hybrid, office attendance, geographic restrictions, evidence. “Home office possible” does not establish fully remote work. |
| Responsibilities | Specific duties supported by the description, kept separate from candidate requirements and LinkedIn's job-function label. |
| Seniority and employment | Description-supported claims alongside the preserved LinkedIn criteria, with explicit conflicts. |
| Education, compensation, benefits | Add after the core outputs have been reviewed. Preserve degree alternatives; original salary amount, currency, period, gross/net basis, and conditions. Do not invent EUR conversions or annualize an unknown period. |

Use `required`, `preferred`, `mentioned`, `not_required`, and `unclear` for requirement strength. Preserve raw terms and use a small, versioned alias map for safe normalization, such as “PowerBI” to “Power BI”. A list item mentioned in employer marketing is not automatically a candidate requirement.

For each field group, distinguish `stated`, `not_stated`, `conflicting`, and `not_assessable`. The last state covers incomplete or unusable input. Empty lists mean no supported items were found, never that a qualification is unnecessary. Processing failures belong in queue status, not in these extraction values.

Keep provenance and validation state outside the model-generated business fields. Track three distinct checks: schema accepted, source evidence checked, and human reviewed. A successful API response does not imply all three. Structured Outputs supports the shape of the result but can still contain factual mistakes; see [handling mistakes](https://developers.openai.com/api/docs/guides/structured-outputs#handling-mistakes).

## 3. Implemented extraction prompt

The [prompt module](backend/src/ai/prompt.py) is the single implementation. Pydantic owns the schema. The prompt defines source boundaries, negation, alternatives, unknown values, conflicts, and multilingual behavior. It separates description language from applicant language requirements. Old AI output is never supplied as factual input, and posting content is treated as data rather than instructions.

Add examples for observed output failures. Follow the explicit ambiguity and evidence handling in [GPT-5.4 mini prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4#prompting-best-practices).

## 4. Queue, storage, and repeatability

- Extend the existing description-source and extraction storage. Add one durable AI work queue with attempt history. Use one worker coordinator and a bounded pool of 100 async requests; keep SQLite transactions short and serialize writes. A restart must not create another pool alongside a surviving coordinator.
- Support queued, running, retry-wait, succeeded, failed, and interrupted work, plus pause/resume and retry-failed controls. Pausing stops new admissions while in-flight requests finish. Persist work before dispatch and results as they arrive. Do not hold a database transaction open during an API request.
- Retry transient rate limits, Flex capacity errors, network failures, and server errors with bounded exponential backoff and jitter, respecting `Retry-After`. Start with at most five attempts per item; keep exhausted work visible for manual retry. Authentication, quota, unsupported configuration, refusals, incomplete responses, and validation failures need distinct handling rather than an endless retry loop. See [rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits#retrying-with-exponential-backoff).
- Fingerprint the exact semantic input, including title, company, location, listed criteria, and description, together with requested model, reasoning setting, prompt hash, schema hash, and normalization version. Changes to any of these can require a new extraction. Save the actual returned model and source linkage with each result; an alias alone cannot guarantee a fixed model revision.
- Cache and reuse only equivalent inputs. Identical descriptions in different cities may have different metadata. Consolidated jobs can share a result only when the complete extraction input is identical and evidence references still resolve. Keep posting-specific criteria and conflicts visible.
- Keep results immutable. Select results against the current source and extraction contract, so a slower response for an old source cannot replace a newer result. A failed refresh preserves the previous usable result, labelled stale when appropriate. AI output must never replace the saved source text or personal annotations.
- Make local completion idempotent with a unique work fingerprint. Record request/response IDs and ambiguous timeout outcomes: local deduplication cannot guarantee that a remote request was never processed or charged twice after a connection failure.
- Record latency, attempt counts, errors, input/output/cached/reasoning token usage, requested/actual service tier, and estimated cost per run. Keep stable prompt prefixes reusable. Track spend without imposing an output token cap; reasoning tokens are part of output usage and must not be counted twice. Keep API credentials out of stored inputs and logs.

## 5. Continuity and output testing

Original sources and personal annotations remain unchanged. The job panel exposes historical parsed fields as unvalidated and the old review status/priority. Dry-run placeholders are excluded. Older saved text has unknown completeness; extract supported statements without claiming the source is complete.

Offline tests cover the fixed 10-job cohort, repeat enqueue, restart recovery, the 100-request ceiling, shared backoff, SDK request settings, malformed/incomplete outputs, quotation validation, metadata changes, prior-result preservation, legacy visibility, and safe evidence rendering. These test output contracts and workflow behavior; there is no separate model benchmark or 80-job evaluation project.

Review the 10 real outputs in the Jobs panel: description-language ordering, listed criteria, requirement strength, alternatives, numeric experience, missing values, and highlighted evidence. Correct concrete output failures before removing the trial block. Keep extraction independent of automatic archive decisions.

## Next steps after the pilot

1. Review the trial outputs and address observed errors; then remove the temporary limit and queue remaining saved jobs.
2. Restore Intelligence distributions with extraction coverage and one count per consolidated role. Unknown and conflicting values stay visible and reviewable.
3. Add education, compensation, and benefits when useful. Preserve salary currency, period, gross/net basis, and conditions without invented conversions.
4. Consider new summaries and personal fit ranking separately after the extracted fields are useful in daily review.
