from __future__ import annotations

import requests

from agents.settings import Settings


class PiperSpeechAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def available_voices(self) -> dict[str, object]:
        response = requests.get(
            f"{self.settings.whisper_url}/voices",
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.speech_timeout_seconds,
            ),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise RuntimeError(
                "O serviço Piper respondeu com redirecionamento; por segurança, "
                "o agente não seguirá para outro endereço."
            )
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("default"), str)
            or not isinstance(payload.get("voices"), list)
        ):
            raise RuntimeError("O serviço Piper retornou um catálogo de vozes inválido.")
        return payload

    def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        if not text.strip() or len(text) > 4000:
            raise ValueError("O texto para síntese deve conter de 1 a 4000 caracteres.")
        response = requests.post(
            f"{self.settings.whisper_url}/synthesize",
            json={"text": text, "voice_id": voice_id} if voice_id else {"text": text},
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.speech_timeout_seconds,
            ),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise RuntimeError(
                "O serviço Piper respondeu com redirecionamento; por segurança, "
                "o agente não seguirá para outro endereço."
            )
        if response.status_code == 400:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise ValueError(str(detail or "Requisição de síntese inválida."))
        response.raise_for_status()
        if (
            not response.content.startswith(b"RIFF")
            or b"WAVE" not in response.content[:16]
        ):
            raise RuntimeError("O serviço Piper retornou um WAV inválido.")
        return response.content
