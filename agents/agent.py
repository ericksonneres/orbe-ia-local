from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests

from agents.adapters.comfyui import ComfyUIAdapter
from agents.adapters.ollama import OllamaAdapter
from agents.adapters.whisper import WhisperAdapter
from agents.port_forwarder import LocalPortForwarder
from agents.settings import Settings

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "outputs"
SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}


def check_services(settings: Settings, generate_image: bool) -> None:
    try:
        whisper_response = requests.get(
            f"{settings.whisper_url}/health",
            timeout=(
                settings.connect_timeout_seconds,
                settings.connect_timeout_seconds,
            ),
            allow_redirects=False,
        )
        _raise_for_status(whisper_response, "Whisper")
        whisper_health = whisper_response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(
            f"Whisper não respondeu corretamente em {settings.whisper_url}: {exc}"
        ) from exc
    if not isinstance(whisper_health, dict) or whisper_health.get("status") != "ok":
        raise RuntimeError(f"Whisper não está pronto em {settings.whisper_url}.")

    try:
        ollama_response = requests.get(
            f"{settings.ollama_url}/api/tags",
            timeout=(
                settings.connect_timeout_seconds,
                settings.connect_timeout_seconds,
            ),
            allow_redirects=False,
        )
        _raise_for_status(ollama_response, "Ollama")
        ollama_tags = ollama_response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(
            f"Ollama não respondeu corretamente em {settings.ollama_url}: {exc}"
        ) from exc
    models = ollama_tags.get("models") if isinstance(ollama_tags, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama retornou uma lista de modelos inválida.")
    available_models = {
        model["name"]
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }
    if settings.ollama_model not in available_models:
        raise RuntimeError(
            f"O modelo Ollama {settings.ollama_model!r} não está instalado. "
            "Confira OLLAMA_MODEL ou baixe o modelo com `ollama pull`."
        )

    if generate_image:
        try:
            comfyui_request("GET", "/system_stats", settings)
            checkpoint_info = comfyui_request(
                "GET",
                "/object_info/CheckpointLoaderSimple",
                settings,
            )
        except (RuntimeError, subprocess.SubprocessError, TimeoutError) as exc:
            raise RuntimeError(
                f"ComfyUI não está acessível no container "
                f"{settings.comfyui_container!r}: {exc}"
            ) from exc
        loader_info = checkpoint_info.get("CheckpointLoaderSimple")
        required_inputs = (
            loader_info.get("input", {}).get("required", {})
            if isinstance(loader_info, dict)
            else None
        )
        checkpoint_input = (
            required_inputs.get("ckpt_name")
            if isinstance(required_inputs, dict)
            else None
        )
        available_checkpoints = (
            checkpoint_input[0]
            if isinstance(checkpoint_input, list)
            and checkpoint_input
            and isinstance(checkpoint_input[0], list)
            else None
        )
        if not isinstance(available_checkpoints, list) or not all(
            isinstance(name, str) for name in available_checkpoints
        ):
            raise RuntimeError(
                "ComfyUI não retornou uma lista válida de checkpoints disponíveis."
            )
        if settings.checkpoint not in available_checkpoints:
            raise RuntimeError(
                f"O checkpoint ComfyUI {settings.checkpoint!r} não está disponível. "
                "Confira COMFYUI_CHECKPOINT e os arquivos em "
                "`stable-diffusion/models/checkpoints/`."
            )


def _raise_for_status(response: requests.Response, service: str) -> None:
    if 300 <= response.status_code < 400:
        raise RuntimeError(
            f"{service} respondeu com redirecionamento; por segurança, "
            "o agente não seguirá para outro endereço."
        )
    response.raise_for_status()


def transcribe_audio(audio_path: Path, settings: Settings) -> dict[str, object]:
    return WhisperAdapter(settings).transcribe_path(audio_path)


def _ollama_chat(
    system_prompt: str,
    user_content: str,
    temperature: float,
    empty_response_error: str,
    settings: Settings,
) -> str:
    return OllamaAdapter(settings).chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        settings.ollama_model,
        temperature=temperature,
        empty_response_error=empty_response_error,
    )


def answer_with_ollama(transcript: str, settings: Settings) -> str:
    return _ollama_chat(
        "Você é um assistente útil. Responda sempre em português do Brasil, "
        "com clareza e sem inventar informações.",
        transcript,
        temperature=0.4,
        empty_response_error="Ollama retornou uma resposta vazia.",
        settings=settings,
    )


def make_image_prompt(answer: str, settings: Settings) -> str:
    return _ollama_chat(
        "Create a concise visual prompt in English for Stable Diffusion 1.5. "
        "Describe one coherent, safe image that illustrates the assistant "
        "answer. Return only the prompt.",
        answer,
        temperature=0.2,
        empty_response_error="Ollama não criou um prompt válido para a imagem.",
        settings=settings,
    )


def comfyui_request(
    method: str,
    path: str,
    settings: Settings,
    payload: dict[str, object] | None = None,
    timeout: int | None = None,
) -> dict[str, object]:
    return ComfyUIAdapter(settings).request(
        method, path, payload=payload, timeout=timeout
    )


def create_image(prompt: str, run_id: str, settings: Settings) -> Path:
    return ComfyUIAdapter(settings).create_image(
        prompt,
        run_id,
        output_dir=OUTPUT_DIR,
    )


def run_pipeline(
    audio_path: Path,
    generate_image: bool,
    settings: Settings | None = None,
) -> dict[str, object]:
    if settings is None:
        settings = Settings.from_environment()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Arquivo de áudio não encontrado: {audio_path}")
    if audio_path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValueError(
            f"Formato não suportado ({audio_path.suffix}). "
            f"Use: {', '.join(sorted(SUPPORTED_AUDIO_SUFFIXES))}"
        )

    check_services(settings, generate_image=generate_image)
    audio_result = transcribe_audio(audio_path, settings)
    transcript = str(audio_result["text"]).strip()
    answer = answer_with_ollama(transcript, settings)

    run_id = (
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    )
    text_dir = OUTPUT_DIR / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    answer_path = text_dir / f"{run_id}.txt"
    answer_path.write_text(answer + "\n", encoding="utf-8")

    result: dict[str, object] = {
        "id": run_id,
        "audio": str(audio_path.resolve()),
        "transcript": transcript,
        "transcript_file": audio_result.get("transcript_file"),
        "answer": answer,
        "answer_file": str(answer_path),
        "image_file": None,
    }

    if generate_image:
        image_prompt = make_image_prompt(answer, settings)
        OllamaAdapter(settings).unload(settings.ollama_model)
        image_path = create_image(image_prompt, run_id, settings)
        result["image_prompt"] = image_prompt
        result["image_file"] = str(image_path)

    metadata_path = text_dir / f"{run_id}.json"
    metadata_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["metadata_file"] = str(metadata_path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline local: áudio → Whisper → Ollama → ComfyUI."
    )
    parser.add_argument("audio", type=Path, help="Caminho de um arquivo de áudio")
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="Transcrever e responder sem gerar imagem.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    forwarder = LocalPortForwarder()
    try:
        forwarder.start()
        settings = Settings.from_environment()
        result = run_pipeline(
            args.audio,
            generate_image=not args.no_image,
            settings=settings,
        )
    except (
        requests.RequestException,
        subprocess.SubprocessError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
    ) as exc:
        print(f"Erro no pipeline: {exc}", file=sys.stderr)
        return 1
    finally:
        forwarder.close()

    print(f"Transcrição: {result['transcript']}")
    print(f"Resposta: {result['answer']}")
    print(f"Resposta salva em: {result['answer_file']}")
    if result["image_file"]:
        print(f"Imagem salva em: {result['image_file']}")
    print(f"Metadados salvos em: {result['metadata_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
