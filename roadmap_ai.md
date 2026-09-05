# AI extraction roadmap

Turn saved job postings into useful, traceable information for review and Intelligence. The extraction pipeline is implemented; each operator request queues up to 100 jobs, advancing through the remaining saved jobs. Product priorities remain in [ROADMAP.md](ROADMAP.md).

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

The live pilot confirms Responses, Structured Outputs and Flex access. The concurrency ceiling is tested offline; throughput at 100 live requests remains unmeasured. See the [model specification](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [Flex pricing](https://developers.openai.com/api/docs/pricing?latest-pricing=flex), and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

Each bulk click queues up to 100 active or favorited jobs with usable saved descriptions, with favorites first, followed by latest sighting and job ID. Cached results and already queued jobs do not consume the batch; later clicks advance to the next jobs. Failed-output retries also select up to 100 at a time. A selected-job action can extract any saved posting individually. Starting the app does not queue work automatically; the operator starts each batch.

The first live run completed all 10 jobs, with one retry. Schema 2 reran the same unchanged sources: all 10 completed across 12 attempts, and all 307 saved evidence spans matched. Education is now captured for all 10; the ambiguous experience threshold, remote-possible labels and false seniority conflicts improved. Skill coverage and duplication still need review. Earlier results remain immutable and readable.

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

Use explicit nested models, including `EducationRequirement` and `ExperienceYears`, with `extra="forbid"`, constrained categories, required keys, and nullable values. Enforce semantic checks after parsing as well as structural validation.

| Field group | Useful structure |
| --- | --- |
| Skills, tools, programming languages | One concise English concept per item, original-language evidence, separate proficiency and applicability condition. Certifications and clearances have their own categories. Alternative groups distinguish choices from items needed together. |
| Education | Qualification, explicitly stated degree level, acceptable disciplines, strength, conditions and alternatives. “Studium” does not imply bachelor. |
| Description language | Any language, with ISO codes ordered by prominence: `de/en`, `en`, `fr`, etc. Use description passages, excluding metadata labels and isolated technical terms. |
| Applicant languages | Language, stated proficiency wording, explicit CEFR level if supplied, requirement strength, evidence. Do not convert “fluent” into an invented CEFR level. |
| Experience | Typed numeric bounds: minimum, maximum, target range, ambiguous minimum or explicit exact duration. “At least 1–2” is an ambiguous threshold; a desired 2–4 years is not an eligibility ceiling. “Several years” has no numeric value. Preserve scope and conditions. |
| Work arrangement | Remote/on-site/hybrid/remote possible/unspecified, office attendance, geographic wording and conditions. Mobile working alone means remote possible; hybrid needs explicit support. Generic conditional employer policies do not establish the role's mode. |
| Responsibilities | Specific duties supported by the description, kept separate from candidate requirements and LinkedIn's job-function label. |
| Seniority and employment | Description-supported claims alongside the preserved LinkedIn criteria, with explicit conflicts. |
| Compensation, benefits | Deferred. Preserve original salary amount, currency, period, gross/net basis and conditions when added. Do not invent EUR conversions or annualize an unknown period. |

Use `required`, `preferred`, `mentioned`, `not_required`, and `unclear` for requirement strength. Preserve raw terms and use a small, versioned alias map for safe normalization, such as “PowerBI” to “Power BI”. A list item mentioned in employer marketing is not automatically a candidate requirement.

For each field group, distinguish `stated`, `not_stated`, `conflicting`, and `not_assessable`. The last state covers incomplete or unusable input. Empty lists mean no supported items were found, never that a qualification is unnecessary. Processing failures belong in queue status, not in these extraction values.

Keep provenance and validation state outside the model-generated business fields. Track three distinct checks: schema accepted, source evidence checked, and human reviewed. A successful API response does not imply all three. Structured Outputs supports the shape of the result but can still contain factual mistakes; see [handling mistakes](https://developers.openai.com/api/docs/guides/structured-outputs#handling-mistakes).

## 3. Implemented extraction prompt

The [prompt module](backend/src/ai/prompt.py) is the single implementation. Schema 2 adds concrete examples from the pilot, a qualification-coverage check, and conservative conflict rules: preferred experience and broad seniority labels can coexist. Input serialization preserves paragraph order. Description language remains separate from applicant language requirements. Old AI output is never supplied as factual input, and posting content is treated as data rather than instructions.

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

Offline tests cover 100-job batch progression, retry batches, reuse of completed results, manual archive preservation, repeat enqueue, restart recovery, concurrency, shared backoff, SDK settings, failed outputs, evidence, schema upgrades, source order and safe UI rendering. Responses use the Pydantic-generated strict schema; application validation runs after capturing usage and the draft so rejected business fields do not erase accounting or diagnostics. These test contracts and workflow behavior, not model accuracy.

Sample outputs in the Jobs panel: description-language ordering, listed criteria, requirement strength, alternatives, numeric experience, missing values, and highlighted evidence. Correct concrete output failures before relying on the fields for filters. Keep extraction independent of automatic archive decisions.

## Next steps

1. The operator will start extraction for the remaining saved jobs. Review skill coverage and education/language claims duplicated into requirements before using skills for archive-wide counts or filters.
2. Restore Intelligence distributions with extraction coverage and one count per consolidated role. Unknown and conflicting values stay visible and reviewable.
3. Add compensation and benefits when useful. Preserve salary currency, period, gross/net basis, and conditions without invented conversions.
4. Consider new summaries and personal fit ranking separately after the extracted fields are useful in daily review.
