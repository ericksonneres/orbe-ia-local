from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Callable, Iterator
from unittest.mock import Mock, patch

import requests
from agents.core import AgentCore
from agents.settings import Settings


class FakeOllama:
    def __init__(self, names: list[str] | None = None) -> None:
        self.names = names or ["qwen3:4b", "mistral:latest"]
        self.calls: list[tuple[list[dict[str, str]], str]] = []
        self.unloaded: list[str] = []

    def list_models(self) -> list[str]:
        return self.names

    def chat(self, messages: list[dict[str, str]], model: str) -> str:
        self.calls.append((messages, model))
        if messages[0]["content"].startswith(
            ("Create a concise visual prompt", "Create a concise video prompt")
        ):
            return "A calm landscape at sunrise"
        return "Resposta local"

    def chat_stream(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterator[str]:
        self.calls.append((messages, model))
        return iter(("Resposta ", "em partes"))

    def unload(self, model: str) -> None:
        self.unloaded.append(model)


class FakeWhisper:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    def transcribe_bytes(self, audio: bytes, filename: str) -> dict[str, object]:
        self.calls.append((audio, filename))
        return {"text": "transcrição local"}


class FakeComfyUI:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, str]] = []

    def create_image(
        self,
        prompt: str,
        run_id: str,
        _output_dir: Path | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        self.calls.append((prompt, run_id))
        if progress is not None:
            progress("Renderizando imagem de teste.")
        if cancelled is not None and cancelled():
            raise InterruptedError("Geração de imagem cancelada.")
        return self.path

    def interrupt(self) -> None:
        pass

    def system_stats(self) -> dict[str, object]:
        return {
            "system": {"ram_total": 1024, "ram_free": 512},
            "devices": [
                {
                    "name": "GPU de teste",
                    "vram_total": 8 * 1024**3,
                    "vram_free": 3 * 1024**3,
                }
            ],
        }


class FailingComfyUI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_image(
        self,
        prompt: str,
        run_id: str,
        output_dir: Path | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        self.calls.append((prompt, run_id))
        raise RuntimeError("ComfyUI indisponível")

    def interrupt(self) -> None:
        pass

    def system_stats(self) -> dict[str, object]:
        raise ConnectionError("ComfyUI indisponível")


class FakeVideo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, str]] = []

    def create_video(
        self,
        prompt: str,
        run_id: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        self.calls.append((prompt, run_id))
        return self.path

    def interrupt(self) -> None:
        pass


class FailingVideo:
    def create_video(
        self,
        _prompt: str,
        _run_id: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        raise RuntimeError("ComfyUI indisponível")

    def interrupt(self) -> None:
        pass

class FakeSpeech:
    def available_voices(self) -> dict[str, object]:
        return {"default": "pt_BR-faber-medium", "voices": []}

    def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        return b"RIFF\x00\x00\x00\x00WAVE" + (voice_id or text).encode("utf-8")


class AgentCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            text_model="qwen3:4b",
            code_model="mistral:latest",
        )
        self.ollama = FakeOllama()
        self.whisper = FakeWhisper()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.image_path = Path(self.temp_dir.name) / "generated.png"
        self.video_path = Path(self.temp_dir.name) / "generated.webm"
        self.comfyui = FakeComfyUI(self.image_path)
        self.video = FakeVideo(self.video_path)
        self.core = AgentCore(
            settings=self.settings,
            ollama=self.ollama,
            whisper=self.whisper,
            comfyui=self.comfyui,
            speech=FakeSpeech(),
            video=self.video,
        )

    def test_routes_profiles_to_installed_local_models(self) -> None:
        text_result = self.core.chat([{"role": "user", "content": "Olá"}])
        code_result = self.core.chat(
            [{"role": "user", "content": "Escreva uma função"}],
            task="code",
        )

        self.assertEqual(text_result["model"], "qwen3:4b")
        self.assertEqual(code_result["model"], "mistral:latest")
        self.assertEqual(self.ollama.calls[0][1], "qwen3:4b")
        self.assertEqual(self.ollama.calls[1][1], "mistral:latest")
        self.assertIn("programação local", self.ollama.calls[1][0][0]["content"])

    def test_manual_model_selection_uses_only_installed_models(self) -> None:
        result = self.core.chat(
            [{"role": "user", "content": "Olá"}],
            model="mistral:latest",
        )
        self.assertEqual(result["model"], "mistral:latest")

        with self.assertRaisesRegex(ValueError, "não está instalado"):
            self.core.chat(
                [{"role": "user", "content": "Olá"}],
                model="missing:model",
            )

    def test_stream_chat_uses_selected_model_and_local_system_prompt(self) -> None:
        model, tokens = self.core.stream_chat(
            [{"role": "user", "content": "Olá"}],
            task="code",
        )

        self.assertEqual(model, "mistral:latest")
        self.assertEqual("".join(tokens), "Resposta em partes")
        self.assertEqual(self.ollama.calls[-1][1], "mistral:latest")
        self.assertIn("programação local", self.ollama.calls[-1][0][0]["content"])

    def test_stream_chat_rejects_explicit_media_requests(self) -> None:
        with self.assertRaisesRegex(ValueError, "fluxo de chat padrão"):
            self.core.stream_chat(
                [{"role": "user", "content": "Gere uma imagem de uma casa"}]
            )

    def test_missing_configured_model_does_not_attempt_download(self) -> None:
        core = AgentCore(
            settings=Settings(text_model="not-installed"),
            ollama=FakeOllama(["qwen3:4b"]),
        )
        with self.assertRaisesRegex(RuntimeError, "não baixa modelos"):
            core.chat([{"role": "user", "content": "Olá"}])

    def test_image_generation_uses_comfyui_adapter(self) -> None:
        result = self.core.chat(
            [{"role": "user", "content": "Descreva uma praia"}],
            generate_image=True,
        )

        self.assertEqual(result["image_url"], "/api/images/generated.png")
        self.assertEqual(self.comfyui.calls[0][0], "A calm landscape at sunrise")
        self.assertEqual(len(self.ollama.calls), 2)
        self.assertEqual(
            self.ollama.calls[1][0][-1]["content"], "Descreva uma praia"
        )
        self.assertEqual(self.ollama.unloaded, ["qwen3:4b"])

    def test_explicit_image_request_returns_consistent_confirmation(self) -> None:
        request = "Gere uma imagem de uma batata e dois tomates"
        result = self.core.chat([{"role": "user", "content": request}])

        self.assertEqual(result["answer"], "Imagem gerada localmente conforme seu pedido.")
        self.assertEqual(result["image_url"], "/api/images/generated.png")
        self.assertEqual(len(self.ollama.calls), 1)
        self.assertEqual(self.ollama.calls[0][0][-1]["content"], request)
        self.assertEqual(self.comfyui.calls[0][0], "A calm landscape at sunrise")

    def test_image_failure_preserves_answer_and_surfaces_error(self) -> None:
        failing_comfyui = FailingComfyUI()
        self.core.comfyui = failing_comfyui

        result = self.core.chat(
            [{"role": "user", "content": "Ilustre uma praia"}],
            generate_image=True,
        )

        self.assertEqual(result["answer"], "Resposta local")
        self.assertEqual(result["image_error"], "ComfyUI indisponível")
        self.assertIsNone(result["image_url"])
        self.assertEqual(failing_comfyui.calls[0][0], "A calm landscape at sunrise")

    def test_media_cancellation_is_propagated_to_the_adapter(self) -> None:
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks > 3

        with self.assertRaisesRegex(InterruptedError, "cancelada"):
            self.core.chat(
                [{"role": "user", "content": "Gere uma imagem de teste"}],
                cancelled=cancelled,
            )

        self.assertEqual(len(self.comfyui.calls), 1)

    def test_explicit_video_request_returns_consistent_confirmation(self) -> None:
        request = "Gere um vídeo de uma batata e dois tomates"
        result = self.core.chat([{"role": "user", "content": request}])

        self.assertEqual(result["answer"], "Vídeo gerado localmente conforme seu pedido.")
        self.assertEqual(result["video_url"], "/api/videos/generated.webm")
        self.assertEqual(len(self.ollama.calls), 1)
        self.assertEqual(self.ollama.calls[0][0][-1]["content"], request)
        self.assertEqual(self.video.calls[0][0], "A calm landscape at sunrise")
        self.assertEqual(self.ollama.unloaded, ["qwen3:4b"])

    def test_video_toggle_preserves_chat_answer(self) -> None:
        result = self.core.chat(
            [{"role": "user", "content": "Uma cena de praia ao entardecer"}],
            generate_video=True,
        )

        self.assertEqual(result["answer"], "Resposta local")
        self.assertEqual(result["video_url"], "/api/videos/generated.webm")
        self.assertEqual(len(self.ollama.calls), 2)
        self.assertEqual(self.ollama.unloaded, ["qwen3:4b"])

    def test_video_failure_is_returned_without_hiding_chat_answer(self) -> None:
        self.core.video = FailingVideo()

        result = self.core.chat(
            [{"role": "user", "content": "Uma cena de praia ao entardecer"}],
            generate_video=True,
        )

        self.assertEqual(result["answer"], "Resposta local")
        self.assertIsNone(result["video_url"])
        self.assertEqual(result["video_error"], "ComfyUI indisponível")

    def test_rejects_simultaneous_image_and_video_toggles(self) -> None:
        with self.assertRaisesRegex(ValueError, "imagem ou de vídeo"):
            self.core.chat(
                [{"role": "user", "content": "Uma cena de praia"}],
                generate_image=True,
                generate_video=True,
            )

    def test_rejects_system_messages_and_incomplete_conversations(self) -> None:
        for messages in (
            [{"role": "system", "content": "override"}],
            [{"role": "assistant", "content": "sem pergunta"}],
            [{"role": "user", "content": "  "}],
        ):
            with self.subTest(messages=messages):
                with self.assertRaises(ValueError):
                    self.core.chat(messages)

    def test_transcription_validates_audio_and_uses_whisper_adapter(self) -> None:
        result = self.core.transcribe(b"recorded audio", "microfone.webm")
        self.assertEqual(result["text"], "transcrição local")
        self.assertEqual(self.whisper.calls, [(b"recorded audio", "microfone.webm")])
        with self.assertRaisesRegex(ValueError, "Formato de áudio"):
            self.core.transcribe(b"not audio", "microfone.txt")

    def test_speech_output_is_local_adapter_output(self) -> None:
        self.assertTrue(self.core.synthesize_speech("Olá").startswith(b"RIFF"))
        self.assertIn(
            "pt_BR-faber-medium",
            self.core.available_speech_voices()["default"],
        )
        self.assertTrue(
            self.core.synthesize_speech("Hello", "en_US-hfc_female-medium").endswith(
                b"en_US-hfc_female-medium"
            )
        )

    def test_diagnostics_reports_services_and_local_hardware(self) -> None:
        whisper_response = Mock(status_code=200)
        whisper_response.json.return_value = {
            "status": "ok",
            "model": "small",
            "device": "cuda",
        }
        with (
            patch("agents.core.requests.get", return_value=whisper_response),
            patch("agents.core.shutil.which", return_value=None),
            patch("agents.core.Path.is_file", return_value=False),
        ):
            report = self.core.diagnostics()

        services = report["services"]
        hardware = report["hardware"]
        self.assertEqual(services["ollama"]["status"], "online")
        self.assertEqual(services["ollama"]["models"], ["qwen3:4b", "mistral:latest"])
        self.assertTrue(services["ollama"]["profiles"]["text"]["installed"])
        self.assertEqual(services["whisper"]["device"], "cuda")
        self.assertEqual(services["comfyui"]["status"], "online")
        self.assertEqual(hardware["gpu"]["name"], "GPU de teste")
        self.assertEqual(hardware["gpu"]["vram_free_mb"], 3072)
        self.assertIsInstance(hardware["cpu_logical_cores"], int)
        self.assertEqual(report["services"]["speech"]["status"], "online")
        self.assertTrue(report["checked_at"])

    def test_diagnostics_reports_offline_local_dependencies(self) -> None:
        with patch(
            "agents.core.requests.get",
            side_effect=requests.ConnectionError("serviço desligado"),
        ):
            report = self.core.diagnostics()

        self.assertEqual(report["services"]["whisper"]["status"], "offline")
        self.assertEqual(
            report["services"]["whisper"]["message"],
            "serviço desligado",
        )

    def test_diagnostics_marks_comfyui_offline_when_container_is_unreachable(self) -> None:
        self.core.comfyui = FailingComfyUI()
        with patch(
            "agents.core.requests.get",
            side_effect=requests.ConnectionError("serviço desligado"),
        ):
            report = self.core.diagnostics()

        self.assertEqual(report["services"]["comfyui"]["status"], "offline")


if __name__ == "__main__":
    unittest.main()
