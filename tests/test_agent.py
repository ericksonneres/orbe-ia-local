import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents import agent


class RunPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_dir = Path(self.temp_dir.name)
        self.audio_path = self.project_dir / "sample.wav"
        self.audio_path.write_bytes(b"wav fixture")
        self.output_dir = self.project_dir / "outputs"

    def test_rejects_missing_audio_before_calling_services(self) -> None:
        missing_audio = self.project_dir / "missing.wav"
        with patch.object(agent, "transcribe_audio") as transcribe:
            with self.assertRaises(FileNotFoundError):
                agent.run_pipeline(missing_audio, generate_image=False)
        transcribe.assert_not_called()

    def test_rejects_unsupported_audio_before_calling_services(self) -> None:
        unsupported_audio = self.project_dir / "sample.txt"
        unsupported_audio.write_text("not audio", encoding="utf-8")
        with patch.object(agent, "transcribe_audio") as transcribe:
            with self.assertRaises(ValueError):
                agent.run_pipeline(unsupported_audio, generate_image=False)
        transcribe.assert_not_called()

    def test_stops_before_transcription_when_preflight_fails(self) -> None:
        with (
            patch.object(
                agent,
                "check_services",
                side_effect=RuntimeError("Whisper indisponível"),
            ),
            patch.object(agent, "transcribe_audio") as transcribe,
        ):
            with self.assertRaisesRegex(RuntimeError, "Whisper indisponível"):
                agent.run_pipeline(self.audio_path, generate_image=False)
        transcribe.assert_not_called()

    def test_without_image_saves_answer_and_metadata(self) -> None:
        with (
            patch.object(agent, "OUTPUT_DIR", self.output_dir),
            patch.object(agent, "check_services"),
            patch.object(
                agent,
                "transcribe_audio",
                return_value={
                    "text": "  texto reconhecido  ",
                    "transcript_file": "transcript.txt",
                },
            ),
            patch.object(agent, "answer_with_ollama", return_value="resposta"),
            patch.object(agent, "make_image_prompt") as make_image_prompt,
        ):
            result = agent.run_pipeline(self.audio_path, generate_image=False)

        answer_path = Path(result["answer_file"])
        metadata_path = Path(result["metadata_file"])
        self.assertEqual(answer_path.read_text(encoding="utf-8"), "resposta\n")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["transcript"], "texto reconhecido")
        self.assertEqual(metadata["answer"], "resposta")
        self.assertIsNone(metadata["image_file"])
        self.assertIsNone(result["image_file"])
        make_image_prompt.assert_not_called()

    def test_with_image_creates_image_from_answer(self) -> None:
        generated_image = self.project_dir / "generated.png"
        settings = agent.Settings()
        with (
            patch.object(agent, "OUTPUT_DIR", self.output_dir),
            patch.object(agent, "check_services"),
            patch.object(agent, "transcribe_audio", return_value={"text": "texto"}),
            patch.object(agent, "answer_with_ollama", return_value="resposta"),
            patch.object(
                agent, "make_image_prompt", return_value="visual prompt"
            ) as make_image_prompt,
            patch.object(agent, "OllamaAdapter") as ollama_adapter_factory,
            patch.object(
                agent, "create_image", return_value=generated_image
            ) as create_image,
        ):
            result = agent.run_pipeline(
                self.audio_path,
                generate_image=True,
                settings=settings,
            )

        make_image_prompt.assert_called_once_with("resposta", settings)
        ollama_adapter_factory.assert_called_once_with(settings)
        ollama_adapter_factory.return_value.unload.assert_called_once_with(
            settings.ollama_model
        )
        create_image.assert_called_once()
        self.assertEqual(create_image.call_args.args[0], "visual prompt")
        self.assertEqual(create_image.call_args.args[2], settings)
        self.assertEqual(result["image_file"], str(generated_image))
        self.assertEqual(result["image_prompt"], "visual prompt")


class SettingsTests(unittest.TestCase):
    def test_defaults_preserve_current_local_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = agent.Settings.from_environment()

        self.assertEqual(settings.ollama_url, "http://127.0.0.1:11434")
        self.assertEqual(settings.whisper_url, "http://127.0.0.1:8001")
        self.assertEqual(settings.comfyui_container, "stable-diffusion")
        self.assertEqual(settings.comfyui_api_url, "http://127.0.0.1:18188")
        self.assertEqual(settings.ollama_model, "qwen3:4b")
        self.assertEqual(settings.checkpoint, "v1-5-pruned-emaonly.safetensors")
        self.assertEqual(settings.image_timeout_seconds, 300)
        self.assertEqual(settings.video_timeout_seconds, 1800)
        self.assertEqual(settings.speech_timeout_seconds, 120)

    def test_environment_overrides_urls_model_and_timeouts(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OLLAMA_URL": "http://127.0.0.2:11434/",
                "WHISPER_URL": "http://localhost:8001/",
                "COMFYUI_CONTAINER": "comfyui-local",
                "COMFYUI_API_URL": "http://127.0.0.1:18188/",
                "OLLAMA_MODEL": "custom-model",
                "COMFYUI_CHECKPOINT": "custom.safetensors",
                "IMAGE_TIMEOUT_SECONDS": "90",
                "VIDEO_TIMEOUT_SECONDS": "1200",
                "SPEECH_TIMEOUT_SECONDS": "180",
            },
            clear=True,
        ):
            settings = agent.Settings.from_environment()

        self.assertEqual(settings.ollama_url, "http://127.0.0.2:11434")
        self.assertEqual(settings.whisper_url, "http://localhost:8001")
        self.assertEqual(settings.comfyui_container, "comfyui-local")
        self.assertEqual(settings.comfyui_api_url, "http://127.0.0.1:18188")
        self.assertEqual(settings.ollama_model, "custom-model")
        self.assertEqual(settings.checkpoint, "custom.safetensors")
        self.assertEqual(settings.image_timeout_seconds, 90)
        self.assertEqual(settings.video_timeout_seconds, 1200)
        self.assertEqual(settings.speech_timeout_seconds, 180)

    def test_environment_overrides_text_and_code_profiles(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OLLAMA_TEXT_MODEL": "mistral:latest",
                "OLLAMA_CODE_MODEL": "qwen3:4b",
            },
            clear=True,
        ):
            settings = agent.Settings.from_environment()

        self.assertEqual(settings.text_model, "mistral:latest")
        self.assertEqual(settings.code_model, "qwen3:4b")

    def test_rejects_non_positive_timeout(self) -> None:
        with patch.dict(os.environ, {"IMAGE_TIMEOUT_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "IMAGE_TIMEOUT_SECONDS"):
                agent.Settings.from_environment()
        with patch.dict(os.environ, {"VIDEO_TIMEOUT_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "VIDEO_TIMEOUT_SECONDS"):
                agent.Settings.from_environment()
        with patch.dict(os.environ, {"SPEECH_TIMEOUT_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SPEECH_TIMEOUT_SECONDS"):
                agent.Settings.from_environment()

    def test_rejects_remote_service_url(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_URL": "http://ollama.example:11434"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "OLLAMA_URL"):
                agent.Settings.from_environment()

    def test_rejects_non_http_or_url_with_credentials(self) -> None:
        for url in (
            "https://127.0.0.1:11434",
            "http://user:password@127.0.0.1:11434",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    agent.Settings(ollama_url=url)


class OllamaChatTests(unittest.TestCase):
    def test_chat_helper_uses_shared_request_and_returns_content(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"message": {"content": "  resposta  "}}
        settings = agent.Settings(ollama_model="test-model")

        with patch.object(agent.requests, "post", return_value=response) as post:
            content = agent._ollama_chat(
                "system prompt",
                "user prompt",
                temperature=0.25,
                empty_response_error="empty",
                settings=settings,
            )

        self.assertEqual(content, "resposta")
        post.assert_called_once_with(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "user prompt"},
                ],
                "stream": False,
                "options": {"num_ctx": 4096, "temperature": 0.25},
            },
            timeout=(15, 300),
            allow_redirects=False,
        )
        response.raise_for_status.assert_called_once()

    def test_unload_requests_numeric_zero_keep_alive(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"done_reason": "unload"}
        settings = agent.Settings()

        with patch.object(agent.requests, "post", return_value=response) as post:
            agent.OllamaAdapter(settings).unload("qwen3:4b")

        post.assert_called_once_with(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": "qwen3:4b",
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            },
            timeout=(15, 30),
            allow_redirects=False,
        )
        response.raise_for_status.assert_called_once()


class CheckServicesTests(unittest.TestCase):
    @staticmethod
    def response(payload: dict[str, object]) -> Mock:
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def test_checks_whisper_and_model_before_pipeline(self) -> None:
        settings = agent.Settings()
        whisper_response = self.response({"status": "ok"})
        ollama_response = self.response(
            {"models": [{"name": settings.ollama_model}]}
        )

        with (
            patch.object(
                agent.requests,
                "get",
                side_effect=[whisper_response, ollama_response],
            ) as get,
            patch.object(agent, "comfyui_request") as comfyui_request,
        ):
            agent.check_services(settings, generate_image=False)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[0], f"{settings.whisper_url}/health")
        self.assertEqual(
            get.call_args_list[1].args[0],
            f"{settings.ollama_url}/api/tags",
        )
        comfyui_request.assert_not_called()

    def test_checks_comfyui_only_when_image_is_enabled(self) -> None:
        settings = agent.Settings()
        checkpoint_info = {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [[settings.checkpoint, "another.safetensors"]]
                    }
                }
            }
        }
        with (
            patch.object(
                agent.requests,
                "get",
                side_effect=[
                    self.response({"status": "ok"}),
                    self.response({"models": [{"name": settings.ollama_model}]}),
                ],
            ),
            patch.object(
                agent,
                "comfyui_request",
                side_effect=[{}, checkpoint_info],
            ) as comfyui_request,
        ):
            agent.check_services(settings, generate_image=True)

        self.assertEqual(
            comfyui_request.call_args_list,
            [
                unittest.mock.call("GET", "/system_stats", settings),
                unittest.mock.call(
                    "GET", "/object_info/CheckpointLoaderSimple", settings
                ),
            ],
        )

    def test_fails_clearly_when_checkpoint_is_missing(self) -> None:
        settings = agent.Settings()
        checkpoint_info = {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [["another.safetensors"]]
                    }
                }
            }
        }
        with (
            patch.object(
                agent.requests,
                "get",
                side_effect=[
                    self.response({"status": "ok"}),
                    self.response({"models": [{"name": settings.ollama_model}]}),
                ],
            ),
            patch.object(
                agent,
                "comfyui_request",
                side_effect=[{}, checkpoint_info],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "checkpoint.*não está disponível"):
                agent.check_services(settings, generate_image=True)

    def test_fails_when_checkpoint_list_response_is_malformed(self) -> None:
        settings = agent.Settings()
        malformed_checkpoint_info = {"CheckpointLoaderSimple": {"input": {}}}
        with (
            patch.object(
                agent.requests,
                "get",
                side_effect=[
                    self.response({"status": "ok"}),
                    self.response({"models": [{"name": settings.ollama_model}]}),
                ],
            ),
            patch.object(
                agent,
                "comfyui_request",
                side_effect=[{}, malformed_checkpoint_info],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "lista válida de checkpoints"):
                agent.check_services(settings, generate_image=True)

    def test_fails_clearly_when_ollama_model_is_missing(self) -> None:
        settings = agent.Settings()
        with patch.object(
            agent.requests,
            "get",
            side_effect=[
                self.response({"status": "ok"}),
                self.response({"models": [{"name": "another-model"}]}),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "não está instalado"):
                agent.check_services(settings, generate_image=False)

    def test_reports_unavailable_whisper_before_processing_audio(self) -> None:
        settings = agent.Settings()
        with patch.object(
            agent.requests,
            "get",
            side_effect=agent.requests.ConnectionError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Whisper não respondeu"):
                agent.check_services(settings, generate_image=False)

    def test_blocks_redirect_from_local_service(self) -> None:
        response = Mock()
        response.status_code = 302

        with self.assertRaisesRegex(RuntimeError, "não seguirá"):
            agent._raise_for_status(response, "Whisper")
        response.raise_for_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
