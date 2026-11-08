from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from agents.settings import Settings


class OllamaAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_models(self) -> list[str]:
        response = requests.get(
            f"{self.settings.ollama_url}/api/tags",
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.connect_timeout_seconds,
            ),
            allow_redirects=False,
        )
        _raise_for_status(response)
        payload = response.json()
        rows = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Ollama retornou uma lista de modelos inválida.")
        names = [
            row["name"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("name"), str)
        ]
        return sorted(names)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.4,
        empty_response_error: str = "Ollama retornou uma resposta vazia.",
    ) -> str:
        response = requests.post(
            f"{self.settings.ollama_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"num_ctx": 4096, "temperature": temperature},
            },
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.ollama_timeout_seconds,
            ),
            allow_redirects=False,
        )
        _raise_for_status(response)
        payload: Any = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        answer = message.get("content") if isinstance(message, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError(empty_response_error)
        return answer.strip()

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.4,
        empty_response_error: str = "Ollama retornou uma resposta vazia.",
    ) -> Iterator[str]:
        response = requests.post(
            f"{self.settings.ollama_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {"num_ctx": 4096, "temperature": temperature},
            },
            timeout=(
                self.settings.connect_timeout_seconds,
                self.settings.ollama_timeout_seconds,
            ),
            allow_redirects=False,
            stream=True,
        )
        try:
            _raise_for_status(response)
            received_content = False
            completed = False
            for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise RuntimeError("Ollama retornou um fragmento de streaming inválido.") from exc
                if not isinstance(payload, dict):
                    raise RuntimeError("Ollama retornou um fragmento de streaming inválido.")
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                message = payload.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if content is not None:
                    if not isinstance(content, str):
                        raise RuntimeError("Ollama retornou um token de texto inválido.")
                    if content:
                        received_content = True
                        yield content
                if payload.get("done") is True:
                    completed = True
                    break
            if not completed:
                raise RuntimeError("Ollama encerrou o streaming sem concluir a resposta.")
            if not received_content:
                raise RuntimeError(empty_response_error)
        finally:
            response.close()

    def unload(self, model: str) -> None:
        response = requests.post(
            f"{self.settings.ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            },
            timeout=(self.settings.connect_timeout_seconds, 30),
            allow_redirects=False,
        )
        _raise_for_status(response)
        payload: Any = response.json()
        if not isinstance(payload, dict) or payload.get("done_reason") != "unload":
            raise RuntimeError(
                f"Ollama não confirmou o descarregamento do modelo {model!r}."
            )


def _raise_for_status(response: requests.Response) -> None:
    if 300 <= response.status_code < 400:
        raise RuntimeError(
            "Ollama respondeu com redirecionamento; por segurança, "
            "o agente não seguirá para outro endereço."
        )
    response.raise_for_status()
