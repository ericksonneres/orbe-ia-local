from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Iterator

import requests

import agents.server as server_module
from agents.server import AgentRequestHandler, create_server
from agents.settings import Settings


class StubCore:
    settings = Settings()

    def available_models(self) -> list[str]:
        return ["qwen3:4b"]

    def diagnostics(self) -> dict[str, object]:
        return {
            "checked_at": "2026-11-07T00:00:00+00:00",
            "services": {"ollama": {"status": "online"}},
            "hardware": {"cpu_logical_cores": 32},
        }

    def is_media_request(
        self,
        messages: list[dict[str, str]],
        generate_image: bool = False,
        generate_video: bool = False,
    ) -> bool:
        request = messages[-1].get("content", "").lower() if messages else ""
        return (
            generate_image
            or generate_video
            or ("gere" in request and ("imagem" in request or "vídeo" in request))
        )

    def cancel_media_generation(self) -> None:
        pass

    def chat(
        self,
        messages: list[dict[str, str]],
        task: str,
        model: str | None,
        generate_image: bool,
        generate_video: bool,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        if callable(progress):
            progress("Renderizando mídia de teste.")
        return {
            "answer": messages[-1]["content"],
            "model": model or "qwen3:4b",
            "task": task,
            "image_requested": generate_image,
            "image_url": None,
            "video_requested": generate_video,
            "video_url": "/api/videos/pilot.webm" if generate_video else None,
        }

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        task: str,
        model: str | None,
    ) -> tuple[str, Iterator[str]]:
        return model or "qwen3:4b", iter(("Resposta ", "em partes"))

    def transcribe(self, audio: bytes, filename: str) -> dict[str, object]:
        return {"text": f"transcribed {filename}", "size": len(audio)}

    def available_speech_voices(self) -> dict[str, object]:
        return {
            "default": "pt_BR-faber-medium",
            "voices": [{"id": "pt_BR-faber-medium", "available": True}],
        }

    def synthesize_speech(self, text: str, voice_id: str | None = None) -> bytes:
        return b"RIFF\x00\x00\x00\x00WAVE" + (voice_id or text).encode("utf-8")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_core = AgentRequestHandler.core
        AgentRequestHandler.core = StubCore()
        cls.temp_dir = TemporaryDirectory()
        cls.httpd = create_server(
            port=0,
            history_path=Path(cls.temp_dir.name) / "history.sqlite3",
        )
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        AgentRequestHandler.core = cls.previous_core
        cls.temp_dir.cleanup()

    def test_health_and_model_list(self) -> None:
        health = requests.get(f"{self.base_url}/api/health", timeout=2)
        models = requests.get(f"{self.base_url}/api/models", timeout=2)
        diagnostics = requests.get(f"{self.base_url}/api/diagnostics", timeout=2)
        voices = requests.get(f"{self.base_url}/api/voices", timeout=2)

        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(models.json()["models"], ["qwen3:4b"])
        self.assertEqual(diagnostics.json()["services"]["ollama"]["status"], "online")
        self.assertEqual(diagnostics.json()["hardware"]["cpu_logical_cores"], 32)
        self.assertEqual(voices.json()["default"], "pt_BR-faber-medium")
        self.assertEqual(health.headers["Cache-Control"], "no-store")

    def test_chat_returns_result_from_core(self) -> None:
        requests.delete(f"{self.base_url}/api/history", timeout=2)
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Olá"}],
                "task": "text",
                "model": None,
                "generate_image": False,
                "generate_video": False,
            },
            timeout=2,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Olá")
        history = requests.get(f"{self.base_url}/api/history", timeout=2)
        self.assertEqual(
            [(item["role"], item["content"]) for item in history.json()["messages"]],
            [("user", "Olá"), ("assistant", "Olá")],
        )

    def test_conversations_can_be_created_renamed_and_deleted(self) -> None:
        requests.delete(f"{self.base_url}/api/history", timeout=2)
        initial = requests.get(f"{self.base_url}/api/conversations", timeout=2)
        first_id = initial.json()["conversations"][0]["id"]
        created = requests.post(
            f"{self.base_url}/api/conversations",
            json={"title": "Teste local"},
            timeout=2,
        )
        second_id = created.json()["conversation"]["id"]
        renamed = requests.patch(
            f"{self.base_url}/api/conversations/{second_id}",
            json={"title": "Conversa renomeada"},
            timeout=2,
        )
        chat = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Isolada"}],
                "conversation_id": second_id,
            },
            timeout=2,
        )
        first_history = requests.get(
            f"{self.base_url}/api/history?conversation_id={first_id}",
            timeout=2,
        )
        second_history = requests.get(
            f"{self.base_url}/api/history?conversation_id={second_id}",
            timeout=2,
        )
        deleted = requests.delete(
            f"{self.base_url}/api/conversations/{second_id}",
            timeout=2,
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(renamed.json()["conversation"]["title"], "Conversa renomeada")
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(first_history.json()["messages"], [])
        self.assertEqual(len(second_history.json()["messages"]), 2)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(deleted.json()["conversation"]["id"], first_id)

    def test_rejects_invalid_conversation_id(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Olá"}],
                "conversation_id": None,
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("conversation_id", response.json()["error"])

    def test_stream_chat_emits_tokens_and_saves_completed_turn(self) -> None:
        requests.delete(f"{self.base_url}/api/history", timeout=2)
        conversation_id = requests.get(
            f"{self.base_url}/api/conversations", timeout=2
        ).json()["conversations"][0]["id"]
        response = requests.post(
            f"{self.base_url}/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Responda aos poucos"}],
                "task": "text",
                "model": None,
                "conversation_id": conversation_id,
            },
            timeout=2,
            stream=True,
        )
        events = [json.loads(line) for line in response.iter_lines() if line]
        history = requests.get(
            f"{self.base_url}/api/history?conversation_id={conversation_id}",
            timeout=2,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Content-Type"],
            "application/x-ndjson; charset=utf-8",
        )
        self.assertEqual(
            [event["token"] for event in events if event["type"] == "token"],
            ["Resposta ", "em partes"],
        )
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["result"]["answer"], "Resposta em partes")
        self.assertEqual(
            [(item["role"], item["content"]) for item in history.json()["messages"]],
            [
                ("user", "Responda aos poucos"),
                ("assistant", "Resposta em partes"),
            ],
        )

    def test_history_can_be_cleared(self) -> None:
        requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Salvar localmente"}],
                "task": "text",
                "model": None,
                "generate_image": False,
                "generate_video": False,
            },
            timeout=2,
        )
        cleared = requests.delete(f"{self.base_url}/api/history", timeout=2)
        history = requests.get(f"{self.base_url}/api/history", timeout=2)

        self.assertEqual(cleared.json(), {"cleared": True})
        self.assertEqual(history.json(), {"messages": []})

    def test_video_result_is_saved_in_local_history(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Uma cena de praia"}],
                "generate_video": True,
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job"]["id"]
        job = self._wait_for_job(job_id)
        history = requests.get(f"{self.base_url}/api/history", timeout=2)
        messages = history.json()["messages"]

        self.assertTrue(job["result"]["video_requested"])
        self.assertEqual(job["result"]["video_url"], "/api/videos/pilot.webm")
        self.assertEqual(messages[-1]["video_url"], "/api/videos/pilot.webm")

    def test_gallery_lists_media_from_local_conversation_history(self) -> None:
        requests.delete(f"{self.base_url}/api/history", timeout=2)
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Uma cena de praia"}],
                "generate_video": True,
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 202)
        self._wait_for_job(response.json()["job"]["id"])

        response = requests.get(f"{self.base_url}/api/gallery", timeout=2)
        items = response.json()["items"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "video")
        self.assertEqual(items[0]["url"], "/api/videos/pilot.webm")
        self.assertEqual(items[0]["prompt"], "Uma cena de praia")
        self.assertEqual(items[0]["conversation_title"], "Uma cena de praia")

    def test_media_jobs_report_progress_and_are_listed(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Uma cena de praia"}],
                "generate_video": True,
            },
            timeout=2,
        )
        job_id = response.json()["job"]["id"]
        listed = requests.get(f"{self.base_url}/api/jobs", timeout=2)
        finished = self._wait_for_job(job_id)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(
            finished["result"]["video_url"], "/api/videos/pilot.webm"
        )
        self.assertEqual(listed.status_code, 200)

    def _wait_for_job(self, job_id: str) -> dict[str, object]:
        import time

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = requests.get(
                f"{self.base_url}/api/jobs/{job_id}",
                timeout=2,
            )
            self.assertEqual(response.status_code, 200)
            job = response.json()["job"]
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail(f"A tarefa {job_id} não terminou dentro do prazo.")

    def test_rejects_invalid_chat_task(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Olá"}],
                "task": "remote",
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("task", response.json()["error"])

    def test_audio_and_speech_endpoints(self) -> None:
        transcription = requests.post(
            f"{self.base_url}/api/transcribe",
            data=b"audio",
            headers={
                "Content-Type": "audio/webm",
                "X-Audio-Filename": "microfone.webm",
            },
            timeout=2,
        )
        speech = requests.post(
            f"{self.base_url}/api/speech",
            json={"text": "Hello", "voice_id": "en_US-hfc_female-medium"},
            timeout=2,
        )
        self.assertEqual(transcription.json()["text"], "transcribed microfone.webm")
        self.assertTrue(speech.content.endswith(b"en_US-hfc_female-medium"))
        self.assertEqual(speech.headers["Content-Type"], "audio/wav")

    def test_rejects_non_string_speech_voice(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/speech",
            json={"text": "Olá", "voice_id": 42},
            timeout=2,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("voice_id", response.json()["error"])

    def test_rejects_nonlocal_host_and_cross_origin_posts(self) -> None:
        rejected_host = requests.get(
            f"{self.base_url}/api/health",
            headers={"Host": "attacker.example"},
            timeout=2,
        )
        rejected_origin = requests.post(
            f"{self.base_url}/api/chat",
            json={"messages": [{"role": "user", "content": "Olá"}]},
            headers={"Origin": "http://attacker.example"},
            timeout=2,
        )
        self.assertEqual(rejected_host.status_code, 403)
        self.assertEqual(rejected_origin.status_code, 403)

    def test_server_rejects_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "localhost/loopback"):
            create_server(host="0.0.0.0", port=8766)

    def test_image_path_is_limited_to_output_directory(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            previous_output = server_module.OUTPUT_DIR
            server_module.OUTPUT_DIR = Path(temporary_dir)
            try:
                response = requests.get(
                    f"{self.base_url}/api/images/../../secret.png",
                    timeout=2,
                )
            finally:
                server_module.OUTPUT_DIR = previous_output
        self.assertIn(response.status_code, {400, 404})

    def test_video_is_served_from_local_video_directory(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            previous_output = server_module.OUTPUT_DIR
            server_module.OUTPUT_DIR = Path(temporary_dir)
            video_dir = Path(temporary_dir) / "videos"
            video_dir.mkdir()
            (video_dir / "pilot.webm").write_bytes(b"local video")
            try:
                response = requests.get(
                    f"{self.base_url}/api/videos/pilot.webm",
                    timeout=2,
                )
            finally:
                server_module.OUTPUT_DIR = previous_output

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "video/webm")
        self.assertEqual(response.content, b"local video")

    def test_rejects_non_boolean_generate_video(self) -> None:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Gere um vídeo"}],
                "generate_video": "yes",
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("generate_video", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
