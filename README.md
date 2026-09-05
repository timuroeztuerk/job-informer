# Job Informer

A personal, local tool for discovering Data Scientist and Data Analyst jobs across Germany and Switzerland.

**Collect → filter → read → review → inspect**

FastAPI and React/TypeScript share a SQLite archive of jobs, repeat sightings, search/run history, filter decisions, original description sources, versioned extractions, and personal flags and favorites.

- LinkedIn collection and description retrieval are manual. Intelligence describes the collected sample.
- Duplicate posting IDs and identical company/title/description matches appear as one job, including across cities. Original postings, locations, links, and review history are preserved.
- Filters use explainable title rules informed by reviewed flags. Senior and ambiguous adjacent roles remain reviewable; archives are recoverable and manual restores take precedence.
- Saved sources—including text from earlier collections—support further extraction without refetching. Failed refreshes preserve usable text; extraction never overwrites personal annotations.
- AI extraction uses GPT-5.4 mini with medium reasoning and Flex processing. The initial 10-job trial shows description languages, structured fields, and source quotations for review.

Next steps are in [ROADMAP.md](ROADMAP.md).
