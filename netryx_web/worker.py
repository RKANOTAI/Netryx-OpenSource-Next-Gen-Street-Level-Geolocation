from __future__ import annotations

import threading
from typing import Any

from .research import ResearchError
from .store import JobStore


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        research_service: Any,
        *,
        photo_research_service: Any | None = None,
        poll_interval: float = 0.1,
    ):
        self.store = store
        self.research_service = research_service
        self.photo_research_service = photo_research_service
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="netryx-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._stop.wait(self.poll_interval)
                continue
            self._run(job)

    def _run(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]

        def progress(phase: str, percent: int | None, message_fr: str) -> None:
            self.store.update_progress(
                job_id,
                phase=phase,
                percent=percent,
                message_fr=message_fr,
            )

        try:
            payload = self.store.get_payload(job_id)
            if payload and payload.get("input_type") == "photo":
                if self.photo_research_service is None:
                    raise ResearchError(
                        "PHOTO_PIPELINE_NOT_CONFIGURED",
                        "Le pipeline de recherche photo n’est pas configuré sur ce serveur.",
                        retryable=False,
                    )
                result = self.photo_research_service.run(job_id, payload, progress)
            else:
                result = self.research_service.run(job["listing_url"], progress)
            if result is None:
                self.store.not_found(
                    job_id,
                    "Aucune correspondance suffisamment fiable n’a été trouvée.",
                )
            else:
                self.store.succeed(job_id, result)
        except ResearchError as exc:
            status = "blocked" if exc.code == "LISTING_ACCESS_BLOCKED" else "failed"
            self.store.fail(
                job_id,
                code=exc.code,
                message_fr=exc.message_fr,
                retryable=exc.retryable,
                status=status,
            )
        except Exception:
            self.store.fail(
                job_id,
                code="INTERNAL_RESEARCH_ERROR",
                message_fr="Le moteur de recherche a rencontré une erreur inattendue.",
                retryable=True,
            )
