# Job Informer

A personal, local tool for discovering Data Scientist and Data Analyst jobs across Germany and Switzerland.

**Collect → filter → read → review → inspect**

FastAPI and React/TypeScript share a SQLite archive of jobs, repeat sightings, search/run history, filter decisions, original description sources, versioned extractions, and personal flags and favorites.

- Desktop-only interface: no mobile navigation or mobile-specific layouts are needed.
- Jobs focuses on search and review, with Description and AI insights tabs and combined job/source history below the content. Collection and processing controls stay collapsed until needed; retrieval is manual. Intelligence describes the collected sample.
- Duplicate posting IDs and identical company/title/description matches appear as one job, including across cities. Original postings, locations, links, and review history are preserved.
- Filters use explainable title rules informed by reviewed flags. Machine Learning Engineer roles are excluded; ML scientist and ordinary senior data roles remain eligible. Manual archives and restores survive collection and reprocessing and follow proven duplicate postings; rebuilding an empty database requires restoring these decisions from backup.
- Description fetching and retries are limited to active jobs, including favorites; queued jobs are checked again before retrieval. Saved sources—including text from earlier collections—remain readable and reparsable after archiving. Failed refreshes preserve usable text; extraction never overwrites personal annotations.
- AI extraction uses GPT-5.4 mini with medium reasoning and Flex processing, up to 100 concurrent requests. Bulk extraction, single-job requests and retries accept only active jobs; favorites never bypass filters or archives. Batches contain up to 100 jobs, and eligibility is checked again before each request and before publishing its result. Saved results and usage history remain available after archiving.

Next steps are in [ROADMAP.md](ROADMAP.md).
