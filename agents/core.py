from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol
from uuid import uuid4

import requests

from agents.adapters.comfyui import OUTPUT_DIR, ComfyUIAdapter
from agents.adapters.comfyui_video import ComfyUIVideoAdapter
from agents.adapters.ollama import OllamaAdapter
from agents.adapters.speech import PiperSpeechAdapter
from agents.adapters.whisper import WhisperAdapter
from agents.settings import Settings

Task = Literal["text", "code"]
SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
IMAGE_REQUEST_PATTERN = re.compile(
    r"\b(?:gere|gera|crie|cria|desenhe|desenha|produza|produz|faça|faz|"
    r"generate|create|draw)\b.{0,80}\b"
    r"(?:imagem|ilustração|figura|foto|image|illustration|picture)\b",
    re.IGNORECASE,
)
VIDEO_REQUEST_PATTERN = re.compile(
    r"\b(?:gere|gera|crie|cria|produza|produz|faça|faz|"
    r"generate|create|make)\b.{0,80}\b(?:vídeo|video)\b",
    re.IGNORECASE,
)


class OllamaPort(Protocol):
    def list_models(self) -> list[str]: ...

    def chat(self, messages: list[dict[str, str]], model: str) -> str: ...

    def chat_stream(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterator[str]: ...

    def unload(self, model: str) -> None: ...


class WhisperPort(Protocol):
    def transcribe_bytes(self, audio: bytes, filename: str) -> dict[str, object]: ...


class ComfyUIPort(Protocol):
    def create_image(
        self,
        prompt: str,
        run_id: str,
        output_dir: Path | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path: ...

    def system_stats(self) -> dict[str, object]: ...

    def interrupt(self) -> None: ...


class VideoPort(Protocol):
    def create_video(
        self,
        prompt: str,
        run_id: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path: ...

    def interrupt(self) -> None: ...


class SpeechPort(Protocol):
    def available_voices(self) -> dict[str, object]: ...

    def synthesize(self, text: str, voice_id: str | None = None) -> bytes: ...


class AgentCore:
    def __init__(
        self,
        settings: Settings | None = None,
        ollama: OllamaPort | None = None,
        whisper: WhisperPort | None = None,
        comfyui: ComfyUIPort | None = None,
        speech: SpeechPort | None = None,
        video: VideoPort | None = None,
    ) -> None:
        self.settings = settings or Settings.from_environment()
        self.ollama = ollama if ollama is not None else OllamaAdapter(self.settings)
        self.whisper = whisper if whisper is not None else WhisperAdapter(self.settings)
        self.comfyui = comfyui if comfyui is not None else ComfyUIAdapter(self.settings)
        self.speech = speech if speech is not None else PiperSpeechAdapter(self.settings)
        self.video = video if video is not None else ComfyUIVideoAdapter(self.settings)

    def available_models(self) -> list[str]:
        return self.ollama.list_models()

    @staticmethod
    def is_media_request(
        messages: list[dict[str, str]],
        generate_image: bool = False,
        generate_video: bool = False,
    ) -> bool:
        if generate_image or generate_video or not messages:
            return generate_image or generate_video
        last_message = messages[-1]
        if not isinstance(last_message, dict):
            return False
        content = last_message.get("content")
        if not isinstance(content, str):
            return False
        return bool(
            IMAGE_REQUEST_PATTERN.search(content)
            or VIDEO_REQUEST_PATTERN.search(content)
        )

    def cancel_media_generation(self) -> None:
        errors: list[str] = []
        for adapter in (self.comfyui, self.video):
            try:
                adapter.interrupt()
            except (OSError, RuntimeError, TimeoutError, requests.RequestException) as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def diagnostics(self) -> dict[str, object]:
        models = self._probe_service(self._diagnose_ollama)
        whisper = self._probe_service(self._diagnose_whisper)
        speech = self._probe_service(self._diagnose_speech)
        comfyui = self._probe_service(self._diagnose_comfyui)
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "services": {
                "ollama": models,
                "whisper": whisper,
                "speech": speech,
                "comfyui": comfyui,
            },
            "hardware": {
                "cpu_logical_cores": os.cpu_count(),
                "memory": self._memory_diagnostics(),
                "gpu": self._gpu_diagnostics(comfyui),
                "disk": self._disk_diagnostics(),
            },
        }

    def _diagnose_ollama(self) -> dict[str, object]:
        models = self.available_models()
        return {
            "message": "Ollama respondeu.",
            "models": models,
            "profiles": {
                "text": {
                    "model": self.settings.text_model,
                    "installed": self.settings.text_model in models,
                },
                "code": {
                    "model": self.settings.code_model,
                    "installed": self.settings.code_model in models,
                },
            },
        }

    def _diagnose_whisper(self) -> dict[str, object]:
        response = requests.get(
            f"{self.settings.whisper_url}/health",
            timeout=(2, 2),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise RuntimeError("Whisper respondeu com redirecionamento.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise RuntimeError("Whisper retornou um estado de saúde inválido.")
        return {
            "message": "Whisper respondeu.",
            "model": payload.get("model"),
            "device": payload.get("device"),
        }

    def _diagnose_speech(self) -> dict[str, object]:
        payload = self.available_speech_voices()
        voices = payload.get("voices")
        if not isinstance(voices, list):
            raise RuntimeError("O serviço de voz retornou uma lista inválida.")
        available = [
            voice
            for voice in voices
            if isinstance(voice, dict) and voice.get("available") is True
        ]
        engines = sorted(
            {
                str(voice.get("engine", "piper")).capitalize()
                for voice in available
                if isinstance(voice, dict)
            }
        )
        return {
            "message": "Serviço de síntese de voz respondeu.",
            "available_voices": len(available),
            "default_voice": payload.get("default"),
            "engines": engines,
        }

    def _diagnose_comfyui(self) -> dict[str, object]:
        stats = self.comfyui.system_stats()
        system = stats.get("system")
        devices = stats.get("devices")
        if not isinstance(system, dict) or not isinstance(devices, list):
            raise RuntimeError("ComfyUI retornou estatísticas de sistema inválidas.")
        return {
            "message": "ComfyUI respondeu.",
            "devices": devices,
            "system": system,
            "checkpoint": self.settings.checkpoint,
        }

    @staticmethod
    def _probe_service(probe: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return {"status": "online", **probe()}
        except (requests.ConnectionError, requests.Timeout, TimeoutError, OSError) as exc:
            return {"status": "offline", "message": str(exc)}
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _memory_diagnostics() -> dict[str, int | None]:
        try:
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0])
        except (OSError, UnicodeError, ValueError):
            return {"total_mb": None, "available_mb": None}
        return {
            "total_mb": values.get("MemTotal", 0) // 1024 or None,
            "available_mb": values.get("MemAvailable", 0) // 1024 or None,
        }

    @staticmethod
    def _disk_diagnostics() -> dict[str, object]:
        try:
            usage = shutil.disk_usage(OUTPUT_DIR.parent)
        except OSError as exc:
            return {"status": "error", "message": str(exc)}
        return {
            "status": "available",
            "path": str(OUTPUT_DIR.parent),
            "total_gb": round(usage.total / 1024**3, 1),
            "free_gb": round(usage.free / 1024**3, 1),
        }

    @staticmethod
    def _gpu_diagnostics(comfyui: dict[str, object]) -> dict[str, object]:
        executable = shutil.which("nvidia-smi")
        if executable is None:
            wsl_executable = Path("/usr/lib/wsl/lib/nvidia-smi")
            executable = str(wsl_executable) if wsl_executable.is_file() else None
        if executable is not None:
            try:
                result = subprocess.run(
                    [
                        executable,
                        "--query-gpu=name,memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if result is not None and result.returncode == 0:
                for line in result.stdout.splitlines():
                    name, separator, metrics = line.partition(",")
                    values = metrics.split(",")
                    if separator and len(values) == 2:
                        try:
                            total, free = (int(value.strip()) for value in values)
                        except ValueError:
                            break
                        return {
                            "status": "available",
                            "name": name.strip(),
                            "vram_total_mb": total,
                            "vram_free_mb": free,
                        }

        if comfyui.get("status") != "online":
            return {
                "status": "unavailable",
                "message": "GPU indisponível: nvidia-smi e ComfyUI não responderam.",
            }
        devices = comfyui.get("devices")
        if not isinstance(devices, list):
            return {
                "status": "unavailable",
                "message": "ComfyUI não informou métricas de GPU.",
            }
        for device in devices:
            if not isinstance(device, dict):
                continue
            name = device.get("name")
            total = device.get("vram_total")
            free = device.get("vram_free")
            if isinstance(total, int) and isinstance(free, int):
                return {
                    "status": "available",
                    "name": name or "GPU reportada pelo ComfyUI",
                    "vram_total_mb": total // 1024**2,
                    "vram_free_mb": free // 1024**2,
                }
        return {
            "status": "unavailable",
            "message": "ComfyUI não informou uma GPU ou métricas de VRAM.",
        }

    def model_for(self, task: Task, requested_model: str | None) -> str:
        available = self.available_models()
        model = requested_model or (
            self.settings.code_model if task == "code" else self.settings.text_model
        )
        if model not in available:
            if requested_model:
                raise ValueError(
                    f"O modelo selecionado {model!r} não está instalado no Ollama."
                )
            raise RuntimeError(
                f"O modelo configurado para {task} ({model!r}) não está instalado. "
                "A aplicação não baixa modelos automaticamente."
            )
        return model

    def chat(
        self,
        messages: list[dict[str, str]],
        task: Task = "text",
        model: str | None = None,
        generate_image: bool = False,
        generate_video: bool = False,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        normalized = self._validate_messages(messages)
        self._raise_if_cancelled(cancelled)
        selected_model = self.model_for(task, model)
        last_request = normalized[-1]["content"]
        video_request = VIDEO_REQUEST_PATTERN.search(last_request)
        image_request = IMAGE_REQUEST_PATTERN.search(last_request)
        if video_request:
            return self._generate_video_result(
                last_request, selected_model, task, progress, cancelled
            )
        if image_request:
            return self._generate_image_result(
                last_request, selected_model, task, progress, cancelled
            )
        if generate_image and generate_video:
            raise ValueError("Escolha geração de imagem ou de vídeo, não ambas.")
        if progress is not None:
            progress("Gerando resposta com o modelo local.")
        answer = self.ollama.chat(
            [
                {"role": "system", "content": self._system_prompt(task)},
                *normalized,
            ],
            selected_model,
        )
        self._raise_if_cancelled(cancelled)
        result: dict[str, object] = {
            "answer": answer,
            "model": selected_model,
            "task": task,
            "image_url": None,
            "video_url": None,
        }
        if generate_image:
            image_result = self._generate_image_result(
                last_request, selected_model, task, progress, cancelled
            )
            result["image_url"] = image_result["image_url"]
            if "image_error" in image_result:
                result["image_error"] = image_result["image_error"]
        if generate_video:
            video_result = self._create_video_result(
                last_request, selected_model, progress, cancelled
            )
            result["video_url"] = video_result["video_url"]
            if "video_error" in video_result:
                result["video_error"] = video_result["video_error"]
        return result

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        task: Task = "text",
        model: str | None = None,
    ) -> tuple[str, Iterator[str]]:
        normalized = self._validate_messages(messages)
        last_request = normalized[-1]["content"]
        if VIDEO_REQUEST_PATTERN.search(last_request) or IMAGE_REQUEST_PATTERN.search(
            last_request
        ):
            raise ValueError("Pedidos de mídia devem usar o fluxo de chat padrão.")
        selected_model = self.model_for(task, model)
        return selected_model, self.ollama.chat_stream(
            [
                {"role": "system", "content": self._system_prompt(task)},
                *normalized,
            ],
            selected_model,
        )

    @staticmethod
    def _system_prompt(task: Task) -> str:
        return (
            "Você é um assistente de programação local. Responda em português "
            "do Brasil, explique decisões com clareza, produza código correto e "
            "seguro quando solicitado e informe incertezas."
            if task == "code"
            else "Você é um assistente útil. Responda em português do Brasil, "
            "com clareza e sem inventar informações."
        )

    def _generate_image_result(
        self,
        request: str,
        model: str,
        task: Task,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "answer": "Não foi possível gerar a imagem localmente.",
            "model": model,
            "task": task,
            "image_url": None,
            "video_url": None,
        }
        try:
            self._raise_if_cancelled(cancelled)
            if progress is not None:
                progress("Preparando prompt de imagem.")
            visual_prompt = self.ollama.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Create a concise visual prompt in English for "
                            "Stable Diffusion 1.5 based on the user's image request. "
                            "Preserve the requested subjects, quantities, and scene. "
                            "Return only the prompt."
                        ),
                    },
                    {"role": "user", "content": request},
                ],
                model,
            )
            self._raise_if_cancelled(cancelled)
            self.ollama.unload(model)
            run_id = (
                f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_"
                f"{uuid4().hex[:8]}"
            )
            image_path = self.comfyui.create_image(
                visual_prompt,
                run_id,
                progress=progress,
                cancelled=cancelled,
            )
        except InterruptedError:
            raise
        except (requests.RequestException, RuntimeError, OSError) as exc:
            result["image_error"] = str(exc)
        else:
            result["answer"] = "Imagem gerada localmente conforme seu pedido."
            result["image_url"] = f"/api/images/{image_path.name}"
        return result

    def _generate_video_result(
        self,
        request: str,
        model: str,
        task: Task,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "answer": "Não foi possível gerar o vídeo localmente.",
            "model": model,
            "task": task,
            "image_url": None,
            "video_url": None,
        }
        video_result = self._create_video_result(request, model, progress, cancelled)
        result.update(video_result)
        if "video_error" not in video_result:
            result["answer"] = "Vídeo gerado localmente conforme seu pedido."
        return result

    def _create_video_result(
        self,
        request: str,
        model: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        try:
            self._raise_if_cancelled(cancelled)
            if progress is not None:
                progress("Preparando prompt de vídeo.")
            visual_prompt = self.ollama.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Create a concise video prompt in English for "
                            "Wan2.1 text-to-video. Preserve the user's requested "
                            "subjects, quantities, action, and scene. Return only "
                            "the prompt."
                        ),
                    },
                    {"role": "user", "content": request},
                ],
                model,
            )
            self._raise_if_cancelled(cancelled)
            self.ollama.unload(model)
            run_id = (
                f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_"
                f"{uuid4().hex[:8]}"
            )
            video_path = self.video.create_video(
                visual_prompt,
                run_id,
                progress=progress,
                cancelled=cancelled,
            )
        except InterruptedError:
            raise
        except (requests.RequestException, RuntimeError, OSError) as exc:
            return {"video_url": None, "video_error": str(exc)}
        return {"video_url": f"/api/videos/{video_path.name}"}

    @staticmethod
    def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
        if cancelled is not None and cancelled():
            raise InterruptedError("Geração de mídia cancelada.")

    def transcribe(self, audio: bytes, filename: str) -> dict[str, object]:
        if not audio:
            raise ValueError("O áudio enviado está vazio.")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_AUDIO_SUFFIXES:
            raise ValueError(
                "Formato de áudio não suportado. Use WAV, MP3, M4A, FLAC, "
                "OGG ou WebM."
            )
        return self.whisper.transcribe_bytes(audio, filename)

    def available_speech_voices(self) -> dict[str, object]:
        return self.speech.available_voices()

    def synthesize_speech(self, text: str, voice_id: str | None = None) -> bytes:
        return self.speech.synthesize(text, voice_id)

    @staticmethod
    def _validate_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        if not isinstance(messages, list) or not messages or len(messages) > 80:
            raise ValueError("A conversa deve conter entre 1 e 80 mensagens.")
        normalized: list[dict[str, str]] = []
        total_chars = 0
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("Cada mensagem da conversa deve ser um objeto.")
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"}:
                raise ValueError("A conversa aceita apenas mensagens user e assistant.")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("O conteúdo da mensagem não pode estar vazio.")
            if len(content) > 12000:
                raise ValueError("Cada mensagem deve ter no máximo 12000 caracteres.")
            total_chars += len(content)
            normalized.append({"role": role, "content": content.strip()})
        if total_chars > 60000:
            raise ValueError("A conversa excede o limite de 60000 caracteres.")
        if normalized[-1]["role"] != "user":
            raise ValueError("A última mensagem da conversa deve ser do usuário.")
        return normalized
