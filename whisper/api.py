from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from faster_whisper import WhisperModel
from piper import PiperVoice

MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
DOWNLOAD_ROOT = Path(os.getenv("WHISPER_DOWNLOAD_ROOT", "/models"))
OUTPUT_DIR = Path(os.getenv("WHISPER_OUTPUT_DIR", "/outputs/audio/transcriptions"))
PIPER_MODEL = Path(
    os.getenv("PIPER_MODEL", "/models/tts/pt_BR-faber-medium.onnx")
)
PIPER_VOICE_DIR = PIPER_MODEL.parent
DEFAULT_VOICE_ID = PIPER_MODEL.stem
KOKORO_MODEL = Path(
    os.getenv("KOKORO_MODEL", "/models/tts/kokoro-v1.0.onnx")
)
KOKORO_VOICES = Path(
    os.getenv("KOKORO_VOICES", "/models/tts/voices-v1.0.bin")
)
SPEECH_VOICES = (
    {
        "id": "pt_BR-faber-medium",
        "name": "Faber",
        "language": "Português (Brasil)",
        "gender": None,
        "quality": "Média",
        "license": "CC0",
        "filename": "pt_BR-faber-medium.onnx",
    },
    {
        "id": "pt_BR-cadu-medium",
        "name": "Cadu",
        "language": "Português (Brasil)",
        "gender": None,
        "quality": "Média",
        "license": "CC0",
        "filename": "pt_BR-cadu-medium.onnx",
    },
    {
        "id": "pt_BR-jeff-medium",
        "name": "Jeff",
        "language": "Português (Brasil)",
        "gender": None,
        "quality": "Média",
        "license": "CC0",
        "filename": "pt_BR-jeff-medium.onnx",
    },
    {
        "id": "pt_BR-edresson-low",
        "name": "Edresson",
        "language": "Português (Brasil)",
        "gender": None,
        "quality": "Baixa",
        "license": "CC BY 4.0",
        "filename": "pt_BR-edresson-low.onnx",
    },
    {
        "id": "pt_BR-dii",
        "name": "Dii",
        "language": "Português (Brasil)",
        "gender": "feminina",
        "quality": "Piper",
        "license": "CC BY-NC-ND 4.0",
        "license_notice": (
            "Uso não comercial com atribuição a TigreGotico Lda; "
            "não modificar nem criar vozes derivadas."
        ),
        "filename": "dii_pt-BR.onnx",
    },
    {
        "id": "en_US-hfc_female-medium",
        "name": "HFC",
        "language": "Inglês (EUA)",
        "gender": "feminina",
        "quality": "Média",
        "license": "CC BY-NC-SA 4.0",
        "filename": "en_US-hfc_female-medium.onnx",
    },
    {
        "id": "en_US-hfc_male-medium",
        "name": "HFC",
        "language": "Inglês (EUA)",
        "gender": "masculina",
        "quality": "Média",
        "license": "CC BY-NC-SA 4.0",
        "filename": "en_US-hfc_male-medium.onnx",
    },
    {
        "id": "es_ES-davefx-medium",
        "name": "Davefx",
        "language": "Espanhol (Espanha)",
        "gender": "masculina",
        "quality": "Média",
        "license": "CC0",
        "filename": "es_ES-davefx-medium.onnx",
    },
    {
        "id": "es_AR-daniela-high",
        "name": "Daniela",
        "language": "Espanhol (Argentina)",
        "gender": "feminina",
        "quality": "Alta",
        "license": "CC BY-SA 4.0",
        "filename": "es_AR-daniela-high.onnx",
    },
    {
        "id": "kokoro-pt_BR-pf_dora",
        "name": "Dora (Kokoro)",
        "language": "Português (Brasil)",
        "gender": "feminina",
        "quality": "Kokoro 82M",
        "license": "Apache-2.0",
        "engine": "kokoro",
        "voice": "pf_dora",
        "language_code": "pt-br",
    },
)
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
MAX_SYNTHESIS_CHARS = 4000


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not PIPER_MODEL.is_file() or not Path(f"{PIPER_MODEL}.json").is_file():
        raise RuntimeError(
            f"Arquivos da voz Piper não encontrados para {PIPER_MODEL}. "
            "Baixe-a com scripts/download-piper-voice.sh."
        )
    app.state.piper_voice = PiperVoice.load(str(PIPER_MODEL), use_cuda=False)
    app.state.piper_voice_id = DEFAULT_VOICE_ID
    app.state.kokoro = None
    app.state.synthesis_lock = threading.Lock()
    app.state.model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        download_root=str(DOWNLOAD_ROOT),
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    app.state.model = None
    app.state.piper_voice = None
    app.state.kokoro = None


app = FastAPI(title="Local Whisper API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "model": MODEL_SIZE,
        "device": DEVICE,
        "speech_voice": PIPER_MODEL.stem,
    }


@app.get("/voices")
def available_voices() -> dict[str, object]:
    return {
        "default": DEFAULT_VOICE_ID,
        "voices": [
            {
                **voice,
                "available": _voice_is_available(voice),
            }
            for voice in SPEECH_VOICES
        ],
    }


def _voice_is_available(voice: dict[str, object]) -> bool:
    if voice.get("engine") == "kokoro":
        return KOKORO_MODEL.is_file() and KOKORO_VOICES.is_file()
    voice_path = PIPER_VOICE_DIR / str(voice["filename"])
    return voice_path.is_file() and Path(f"{voice_path}.json").is_file()


@app.post("/transcribe")
def transcribe(file: Annotated[UploadFile, File()]) -> dict[str, object]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Formato não suportado. Use um destes: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
            while chunk := file.file.read(1024 * 1024):
                temporary_file.write(chunk)

        segments, info = app.state.model.transcribe(
            str(temporary_path),
            language="pt",
            beam_size=5,
            vad_filter=True,
        )
        segment_rows = [
            {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
            for segment in segments
        ]
        transcript = " ".join(row["text"] for row in segment_rows).strip()
        result_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        text_path = OUTPUT_DIR / f"{result_id}.txt"
        json_path = OUTPUT_DIR / f"{result_id}.json"
        text_path.write_text(transcript + "\n", encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "id": result_id,
                    "original_filename": file.filename,
                    "language": info.language,
                    "language_probability": info.language_probability,
                    "text": transcript,
                    "segments": segment_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        return {
            "id": result_id,
            "language": info.language,
            "language_probability": info.language_probability,
            "text": transcript,
            "transcript_file": f"/outputs/audio/transcriptions/{text_path.name}",
            "details_file": f"/outputs/audio/transcriptions/{json_path.name}",
        }
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        file.file.close()


@app.post("/synthesize")
def synthesize(payload: dict[str, object]) -> Response:
    text = payload.get("text")
    voice_id = payload.get("voice_id", DEFAULT_VOICE_ID)
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > MAX_SYNTHESIS_CHARS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"text deve conter de 1 a {MAX_SYNTHESIS_CHARS} caracteres.",
        )
    if not isinstance(voice_id, str):
        raise HTTPException(status_code=400, detail="voice_id deve ser texto.")
    voice_metadata = next(
        (voice for voice in SPEECH_VOICES if voice["id"] == voice_id),
        None,
    )
    if voice_metadata is None:
        raise HTTPException(status_code=400, detail="Voz desconhecida.")

    if voice_metadata.get("engine") == "kokoro":
        if not _voice_is_available(voice_metadata):
            raise HTTPException(
                status_code=400,
                detail=(
                    "A voz Kokoro não está instalada. "
                    "Execute scripts/download-kokoro-voice.sh."
                ),
            )
        with app.state.synthesis_lock:
            if app.state.kokoro is None:
                from kokoro_onnx import Kokoro

                app.state.kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
            samples, sample_rate = app.state.kokoro.create(
                text,
                voice=str(voice_metadata["voice"]),
                speed=1.0,
                lang=str(voice_metadata["language_code"]),
            )
            import soundfile

            audio = io.BytesIO()
            soundfile.write(
                audio,
                samples,
                sample_rate,
                format="WAV",
                subtype="PCM_16",
            )
        return Response(content=audio.getvalue(), media_type="audio/wav")

    voice_path = PIPER_VOICE_DIR / str(voice_metadata["filename"])
    if not voice_path.is_file() or not Path(f"{voice_path}.json").is_file():
        raise HTTPException(
            status_code=400,
            detail=f"A voz {voice_id} não está instalada. "
            "Execute scripts/download-piper-voice.sh.",
        )

    audio = io.BytesIO()
    with app.state.synthesis_lock:
        if app.state.piper_voice_id != voice_id:
            app.state.piper_voice = PiperVoice.load(str(voice_path), use_cuda=False)
            app.state.piper_voice_id = voice_id
        with wave.open(audio, "wb") as wav_file:
            app.state.piper_voice.synthesize_wav(text, wav_file)
    return Response(content=audio.getvalue(), media_type="audio/wav")
