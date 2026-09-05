# Job Informer

A personal, local tool for discovering Data Scientist and Data Analyst jobs across Germany and Switzerland.

**Collect → filter → read → review → inspect**

FastAPI and React/TypeScript share a SQLite archive of jobs, repeat sightings, search/run history, filter decisions, original description sources, versioned extractions, and personal flags and favorites.

- Desktop-only interface: no mobile navigation or mobile-specific layouts are needed.
- Jobs focuses on search and review, with Description and AI insights tabs and combined job/source history below the content. Collection and processing controls stay collapsed until needed; retrieval is manual. Intelligence describes the collected sample.
- Duplicate posting IDs and identical company/title/description matches appear as one job, including across cities. Original postings, locations, links, and review history are preserved.
- Filters use explainable title rules informed by reviewed flags. Ordinary senior and ambiguous adjacent data roles remain reviewable. Manual archives and restores survive collection and reprocessing in the same database; rebuilding an empty database requires restoring these decisions from backup.
- Saved sources—including text from earlier collections—support further extraction without refetching. Failed refreshes preserve usable text; extraction never overwrites personal annotations.
- AI extraction uses GPT-5.4 mini with medium reasoning and Flex processing, up to 100 concurrent requests. Each bulk click queues the next 100 active or favorited jobs with saved descriptions; retries also use batches of 100. Single-job extraction stays available, current results are reused, and archive decisions stay unchanged.

Next steps are in [ROADMAP.md](ROADMAP.md).
