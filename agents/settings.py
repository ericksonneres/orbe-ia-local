from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    ollama_url: str = "http://127.0.0.1:11434"
    whisper_url: str = "http://127.0.0.1:8001"
    comfyui_container: str = "stable-diffusion"
    comfyui_api_url: str = "http://127.0.0.1:18188"
    ollama_model: str = "qwen3:4b"
    text_model: str = "qwen3:4b"
    code_model: str = "qwen3:4b"
    checkpoint: str = "v1-5-pruned-emaonly.safetensors"
    connect_timeout_seconds: int = 15
    ollama_timeout_seconds: int = 300
    whisper_timeout_seconds: int = 600
    speech_timeout_seconds: int = 120
    comfyui_request_timeout_seconds: int = 60
    image_timeout_seconds: int = 300
    video_timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        for name in ("ollama_url", "whisper_url", "comfyui_api_url"):
            _validate_local_http_url(name, getattr(self, name))
        for name in (
            "comfyui_container",
            "ollama_model",
            "text_model",
            "code_model",
            "checkpoint",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"A configuração {name} não pode estar vazia.")
        for name in (
            "connect_timeout_seconds",
            "ollama_timeout_seconds",
            "whisper_timeout_seconds",
            "speech_timeout_seconds",
            "comfyui_request_timeout_seconds",
            "image_timeout_seconds",
            "video_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} deve ser um número inteiro positivo.")

    @classmethod
    def from_environment(cls) -> Settings:
        defaults = cls()
        legacy_model = os.getenv("OLLAMA_MODEL", defaults.ollama_model).strip()
        text_model = os.getenv("OLLAMA_TEXT_MODEL", legacy_model).strip()
        return cls(
            ollama_url=os.getenv("OLLAMA_URL", defaults.ollama_url)
            .strip()
            .rstrip("/"),
            whisper_url=os.getenv("WHISPER_URL", defaults.whisper_url)
            .strip()
            .rstrip("/"),
            comfyui_container=os.getenv(
                "COMFYUI_CONTAINER", defaults.comfyui_container
            ).strip(),
            comfyui_api_url=os.getenv(
                "COMFYUI_API_URL", defaults.comfyui_api_url
            )
            .strip()
            .rstrip("/"),
            ollama_model=legacy_model,
            text_model=text_model,
            code_model=os.getenv("OLLAMA_CODE_MODEL", text_model).strip(),
            checkpoint=os.getenv(
                "COMFYUI_CHECKPOINT", defaults.checkpoint
            ).strip(),
            connect_timeout_seconds=_positive_int_setting(
                "HTTP_CONNECT_TIMEOUT_SECONDS",
                defaults.connect_timeout_seconds,
            ),
            ollama_timeout_seconds=_positive_int_setting(
                "OLLAMA_TIMEOUT_SECONDS", defaults.ollama_timeout_seconds
            ),
            whisper_timeout_seconds=_positive_int_setting(
                "WHISPER_TIMEOUT_SECONDS", defaults.whisper_timeout_seconds
            ),
            speech_timeout_seconds=_positive_int_setting(
                "SPEECH_TIMEOUT_SECONDS", defaults.speech_timeout_seconds
            ),
            comfyui_request_timeout_seconds=_positive_int_setting(
                "COMFYUI_REQUEST_TIMEOUT_SECONDS",
                defaults.comfyui_request_timeout_seconds,
            ),
            image_timeout_seconds=_positive_int_setting(
                "IMAGE_TIMEOUT_SECONDS", defaults.image_timeout_seconds
            ),
            video_timeout_seconds=_positive_int_setting(
                "VIDEO_TIMEOUT_SECONDS", defaults.video_timeout_seconds
            ),
        )


def _validate_local_http_url(name: str, value: str) -> None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} deve ser uma URL HTTP local válida.") from exc
    if (
        parsed.scheme != "http"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            f"{name.upper()} deve ser uma URL HTTP local sem caminho, "
            "credenciais ou parâmetros."
        )
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        raise ValueError(
            f"{name.upper()} deve apontar para localhost ou um endereço IP "
            "de loopback."
        )


def _positive_int_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser um número inteiro positivo.") from exc
    if parsed < 1:
        raise ValueError(f"{name} deve ser um número inteiro positivo.")
    return parsed
