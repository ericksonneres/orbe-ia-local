from __future__ import annotations

import threading
import time
import unittest
from typing import Callable

from agents.jobs import MediaJobQueue


class MediaJobQueueTests(unittest.TestCase):
    def test_runs_job_and_records_result_and_progress(self) -> None:
        queue = MediaJobQueue(lambda: None)
        self.addCleanup(queue.close)
        job = queue.submit(
            lambda progress, _cancelled: (
                progress("Renderizando."),
                {"answer": "concluído"},
            )[1]
        )
        finished = self._wait_for_terminal(queue, str(job["id"]))

        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["result"], {"answer": "concluído"})
        self.assertEqual(finished["progress"], "Geração concluída.")

    def test_reports_worker_errors(self) -> None:
        queue = MediaJobQueue(lambda: None)
        self.addCleanup(queue.close)

        with self.assertLogs("agents.jobs", level="ERROR"):
            job = queue.submit(
                lambda _progress, _cancelled: (_ for _ in ()).throw(
                    ValueError("Falha controlada.")
                )
            )
            finished = self._wait_for_terminal(queue, str(job["id"]))

        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "Falha controlada.")

    def test_cancels_waiting_job_without_interrupting_running_job(self) -> None:
        queue = MediaJobQueue(lambda: self.fail("não deve interromper"))
        release_first = threading.Event()
        self.addCleanup(release_first.set)
        self.addCleanup(queue.close)
        first = queue.submit(
            lambda _progress, _cancelled: (
                release_first.wait(2),
                {"answer": "primeira"},
            )[1]
        )
        self._wait_for_status(queue, str(first["id"]), "running")
        second = queue.submit(
            lambda _progress, _cancelled: {"answer": "segunda"}
        )

        cancelled = queue.cancel(str(second["id"]))
        release_first.set()
        self._wait_for_terminal(queue, str(first["id"]))

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(queue.get(str(second["id"]))["status"], "cancelled")

    def test_cancels_running_job_and_calls_interrupt(self) -> None:
        interrupted = threading.Event()
        queue = MediaJobQueue(interrupted.set)
        self.addCleanup(queue.close)

        def wait_until_cancelled(
            _progress: Callable[[str], None],
            cancelled: Callable[[], bool],
        ) -> dict[str, object]:
            while not cancelled():
                time.sleep(0.005)
            raise InterruptedError("cancelled")

        job = queue.submit(wait_until_cancelled)
        self._wait_for_status(queue, str(job["id"]), "running")
        cancelling = queue.cancel(str(job["id"]))
        finished = self._wait_for_terminal(queue, str(job["id"]))

        self.assertEqual(cancelling["status"], "cancelling")
        self.assertTrue(interrupted.is_set())
        self.assertEqual(finished["status"], "cancelled")

    def _wait_for_status(
        self,
        queue: MediaJobQueue,
        job_id: str,
        status: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = queue.get(job_id)
            if job is not None and job["status"] == status:
                return job
            time.sleep(0.005)
        self.fail(f"A tarefa {job_id} não alcançou o estado {status}.")

    def _wait_for_terminal(
        self,
        queue: MediaJobQueue,
        job_id: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = queue.get(job_id)
            if job is not None and job["status"] in {
                "completed",
                "failed",
                "cancelled",
            }:
                return job
            time.sleep(0.005)
        self.fail(f"A tarefa {job_id} não terminou dentro do prazo.")


if __name__ == "__main__":
    unittest.main()
