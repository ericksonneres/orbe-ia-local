from __future__ import annotations

from pathlib import Path

import requests

from agents.settings import Settings


class WhisperAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def transcribe_bytes(self, audio: bytes, filename: str) -> dict[str, object]:
        response = requests.post(
            f"{self.settings.whisper_url}/transcribe",
            files={"file": (filename, audio)},
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.whisper_timeout_seconds,
            ),
            allow_redirects=False,
        )
        return self._read_transcription(response)

    def transcribe_path(self, path: Path) -> dict[str, object]:
        with path.open("rb") as audio_file:
            response = requests.post(
                f"{self.settings.whisper_url}/transcribe",
                files={"file": (path.name, audio_file)},
                timeout=(
                    self.settings.connect_timeout_seconds,
                    self.settings.whisper_timeout_seconds,
                ),
                allow_redirects=False,
            )
        return self._read_transcription(response)

    @staticmethod
    def _read_transcription(response: requests.Response) -> dict[str, object]:
        if 300 <= response.status_code < 400:
            raise RuntimeError(
                "Whisper respondeu com redirecionamento; por segurança, "
                "o agente não seguirá para outro endereço."
            )
        response.raise_for_status()
        result = response.json()
        transcript = result.get("text") if isinstance(result, dict) else None
        if not isinstance(transcript, str) or not transcript.strip():
            raise RuntimeError("Whisper retornou uma transcrição vazia.")
        return result
