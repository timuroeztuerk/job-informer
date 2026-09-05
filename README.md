# Job Informer

A local, personal tool for collecting LinkedIn jobs, tracking repeat sightings, filtering obvious noise, and reviewing the collected market in a desktop browser.

**Collect → filter → read → review → inspect**

- **Collect:** manually search for Data Scientist and Data Analyst roles across Germany, Switzerland, Berlin, Stuttgart, Frankfurt, and München. Record each posting, repeat encounter, and exact search that found it.
- **Filter:** apply versioned, deterministic title rules with recorded reasons. Reviewed flags shape personal exclusions (such as SAP/CRM, pricing/risk, and director/principal roles); senior roles and ambiguous adjacent work stay reviewable. Automatic archives remain recoverable, and manual restores take precedence.
- **Read:** fetch original descriptions on demand or in small manual batches. Keep the source content, retrieval history, and versioned parsing results; listed job criteria are captured with their evidence. Saved sources can be reparsed without another LinkedIn request.
- **Review:** search and sort jobs, keep favorites, archive or restore postings, and flag unsuitable examples with an optional reason. Flags and favorites survive later collections and archiving. New flags remain review evidence until an explicit rule change is made.
- **Inspect:** explore companies, locations, role mix, and recurring jobs in Intelligence. Rankings link to matching jobs; collection evidence compares complete runs on separate days and shows what each retained city search adds.

FastAPI and React/TypeScript run together locally. SQLite is the canonical store for jobs, observations, query provenance, collection runs, filter decisions, description sources and extractions, and personal flags and favorites. Verified backups protect the archive.

Collection-quality validation is in progress: **two of three comparable collection days** are recorded as of September 5, 2026. All four city searches still add results. Intelligence separates collection coverage from the manual review needed to assess filter quality. See [ROADMAP.md](ROADMAP.md) for the evidence and next steps.

Keep the workflow simple, explainable, and recoverable for one operator. LinkedIn is the only source, collection is always manual, and insights describe the collected sample. Description retrieval pauses on access restrictions and rate limits; failed refreshes preserve the last usable text. Further extraction can build on saved evidence, while AI summaries, scheduling, application tracking, fit scoring, and multi-user features remain outside the current scope.
