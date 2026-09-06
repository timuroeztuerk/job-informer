"""One paced worker for explicitly queued description requests."""

from threading import Event, Lock, Thread

from loguru import logger

from .linkedin import DescriptionError, fetch_source, parse_description
from .store import DescriptionStore


class DescriptionService:
    def __init__(self, db_path, *, fetcher=fetch_source, interval=3.0):
        self.store = DescriptionStore(db_path)
        self.fetcher = fetcher
        self.interval = interval
        self._lock = Lock()
        self._stop = Event()
        self._thread = None

    def start(self):
        with self._lock:
            if self._stop.is_set() or (self._thread and self._thread.is_alive()):
                return
            self._thread = Thread(target=self._work, name="description-retrieval", daemon=True)
            self._thread.start()

    def enqueue(self, job_ids, *, refresh=False):
        added = self.store.enqueue(job_ids, refresh=refresh)
        self.start()
        return added

    def resume(self):
        self.store.resume()
        self.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=35)

    def reparse(self, job_id):
        self.store.require_job(job_id)
        source = self.store.latest_source(job_id, group=True)
        if source is None:
            raise ValueError("Fetch the original description first.")
        data = parse_description(source["body"], source["job_id"])
        self.store.save_extraction(source["source_id"], data)
        return self.store.job_description(job_id)

    def process_one(self) -> bool:
        attempt = self.store.claim()
        if attempt is None:
            return False
        try:
            previous = self.store.latest_source(attempt["job_id"])
            if not self.store.can_fetch(attempt["fetch_id"]):
                return True
            response = self.fetcher(attempt["job_id"], previous)
            if response.status == 304:
                if previous is None:
                    raise DescriptionError("http", "Received an unchanged response without a saved source.")
                source_id, body = previous["source_id"], previous["body"]
            else:
                source_id = self.store.save_source(attempt, response)
                body = response.body
            data = parse_description(body, attempt["job_id"])
            self.store.save_extraction(source_id, data)
            unchanged = previous and source_id == previous["source_id"]
            self.store.finish(attempt["fetch_id"], "unchanged" if unchanged else "succeeded",
                              source_id=source_id, http_status=response.status)
        except DescriptionError as error:
            self.store.finish(attempt["fetch_id"], "failed", http_status=error.http_status,
                              error_kind=error.kind, message=str(error))
            if error.kind in {"blocked", "rate_limited", "identity_mismatch"}:
                self.store.pause(str(error), error.retry_after)
        except Exception:
            logger.exception("Description retrieval failed for {}", attempt["job_id"])
            self.store.finish(attempt["fetch_id"], "failed", error_kind="internal", message="Retrieval could not finish. The previous saved description is preserved.")
            self.store.pause("Retrieval stopped after an internal error. Retry after checking the service.")
        return True

    def _work(self):
        try:
            while not self._stop.is_set():
                if not self.process_one():
                    # Synchronize the empty-queue exit with start/enqueue. A
                    # request arriving during exit must get a fresh worker.
                    with self._lock:
                        state = self.store.queue_status()
                        if state["queued"] and not state["paused"] and not state["fetching"] and not state["cooldown_until"]:
                            continue
                        self._thread = None
                    return
                if self._stop.wait(self.interval):
                    return
        except Exception:
            logger.exception("Description worker stopped")
            self.store.pause("Retrieval stopped. Restart the app to recover the queue.")
