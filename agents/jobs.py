from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal
from uuid import uuid4

JobStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
JobWork = Callable[[Callable[[str], None], Callable[[], bool]], dict[str, object]]

LOGGER = logging.getLogger("agents.jobs")


@dataclass
class MediaJob:
    id: str
    status: JobStatus
    progress: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, object] | None = None
    error: str | None = None
    cancel_error: str | None = None
    cancel_requested: bool = False
    future: Future[None] | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "cancel_error": self.cancel_error,
        }


class MediaJobQueue:
    def __init__(
        self,
        cancel_running: Callable[[], None],
        max_active_jobs: int = 20,
        max_retained_jobs: int = 100,
    ) -> None:
        self._cancel_running = cancel_running
        self._max_active_jobs = max_active_jobs
        self._max_retained_jobs = max_retained_jobs
        self._jobs: dict[str, MediaJob] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="media-generation",
        )

    def submit(self, work: JobWork) -> dict[str, object]:
        now = self._now()
        job = MediaJob(
            id=uuid4().hex,
            status="queued",
            progress="Aguardando a GPU local.",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            active_count = sum(
                item.status in {"queued", "running", "cancelling"}
                for item in self._jobs.values()
            )
            if active_count >= self._max_active_jobs:
                raise OverflowError(
                    f"A fila de mídia atingiu o limite de {self._max_active_jobs} tarefas."
                )
            self._prune_completed_jobs()
            self._jobs[job.id] = job
            job.future = self._executor.submit(self._run, job.id, work)
            return job.as_dict()

    def get(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.as_dict()

    def list_active(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                job.as_dict()
                for job in self._jobs.values()
                if job.status in {"queued", "running", "cancelling"}
            ]

    def cancel(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in {"completed", "failed", "cancelled"}:
                return job.as_dict()
            job.cancel_requested = True
            future = job.future
            if future is not None and future.cancel():
                job.status = "cancelled"
                job.progress = "Tarefa cancelada antes de iniciar."
                job.finished_at = self._now()
                job.updated_at = job.finished_at
                return job.as_dict()
            job.status = "cancelling"
            job.progress = "Solicitando interrupção da geração local."
            job.updated_at = self._now()

        try:
            self._cancel_running()
        except (OSError, RuntimeError, TimeoutError) as exc:
            LOGGER.exception("Não foi possível interromper a geração de mídia.")
            with self._lock:
                job.cancel_error = str(exc)
                job.updated_at = self._now()
        return self.get(job_id)

    def close(self) -> None:
        with self._lock:
            active_job_ids = [
                job.id
                for job in self._jobs.values()
                if job.status in {"queued", "running", "cancelling"}
            ]
        try:
            for job_id in active_job_ids:
                self.cancel(job_id)
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str, work: JobWork) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested:
                self._finish(job, "cancelled", "Tarefa cancelada antes de iniciar.")
                return
            job.status = "running"
            job.progress = "Preparando a geração de mídia."
            job.started_at = self._now()
            job.updated_at = job.started_at

        def update_progress(message: str) -> None:
            with self._lock:
                current = self._jobs[job_id]
                if current.status in {"running", "cancelling"}:
                    current.progress = message
                    current.updated_at = self._now()

        try:
            result = work(update_progress, lambda: self._is_cancelled(job_id))
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_requested:
                    self._finish(job, "cancelled", "Geração cancelada.")
                else:
                    job.error = str(exc) or type(exc).__name__
                    self._finish(job, "failed", "A geração falhou.")
                    LOGGER.exception("A tarefa de mídia %s falhou.", job_id)
            return

        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            if job.cancel_requested:
                self._finish(job, "cancelled", "Geração cancelada.")
                return
            media_error = result.get("image_error") or result.get("video_error")
            if isinstance(media_error, str) and media_error:
                job.error = media_error
                self._finish(job, "failed", "A mídia não foi gerada.")
            else:
                self._finish(job, "completed", "Geração concluída.")

    def _finish(self, job: MediaJob, status: JobStatus, progress: str) -> None:
        now = self._now()
        job.status = status
        job.progress = progress
        job.finished_at = now
        job.updated_at = now

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs[job_id].cancel_requested

    def _prune_completed_jobs(self) -> None:
        excess = len(self._jobs) - self._max_retained_jobs + 1
        if excess <= 0:
            return
        completed = [
            job
            for job in self._jobs.values()
            if job.status in {"completed", "failed", "cancelled"}
        ]
        completed.sort(key=lambda job: job.created_at)
        for job in completed[:excess]:
            self._jobs.pop(job.id, None)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
