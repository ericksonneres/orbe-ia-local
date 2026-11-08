from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import parse_qs, urlsplit

import requests

from agents.adapters.comfyui import OUTPUT_DIR
from agents.core import AgentCore, Task
from agents.history import HistoryStore
from agents.jobs import MediaJobQueue
from agents.port_forwarder import LocalPortForwarder
from agents.settings import Settings

WEB_DIR = Path(__file__).resolve().parent / "web"
LOG_DIR = OUTPUT_DIR / "logs"
LOGGER = logging.getLogger("agents.server")
MAX_JSON_BYTES = 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024
SAFE_IMAGE_NAME = re.compile(r"[A-Za-z0-9._-]+")


class AgentService(Protocol):
    settings: Settings

    def available_models(self) -> list[str]: ...

    def diagnostics(self) -> dict[str, object]: ...

    def is_media_request(
        self,
        messages: list[dict[str, str]],
        generate_image: bool = False,
        generate_video: bool = False,
    ) -> bool: ...

    def cancel_media_generation(self) -> None: ...

    def chat(
        self,
        messages: list[dict[str, str]],
        task: Task,
        model: str | None,
        generate_image: bool,
        generate_video: bool,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]: ...

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        task: Task,
        model: str | None,
    ) -> tuple[str, Iterator[str]]: ...

    def transcribe(self, audio: bytes, filename: str) -> dict[str, object]: ...

    def available_speech_voices(self) -> dict[str, object]: ...

    def synthesize_speech(self, text: str, voice_id: str | None = None) -> bytes: ...


class AgentHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        request_handler: type[BaseHTTPRequestHandler],
        history_path: Path | None,
    ) -> None:
        self.history_store = HistoryStore(
            history_path or OUTPUT_DIR / "agent-history.sqlite3"
        )
        super().__init__(address, request_handler)
        self.job_queue = MediaJobQueue(AgentRequestHandler.core.cancel_media_generation)

    def server_close(self) -> None:
        self.job_queue.close()
        super().server_close()


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "LocalAgent/1.0"
    core: AgentService = AgentCore()

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.info("%s %s", self.client_address[0], format_string % args)

    def do_GET(self) -> None:
        if not self._validate_local_request():
            return
        path = urlsplit(self.path).path
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        if path in static_files:
            filename, content_type = static_files[path]
            self._send_bytes((WEB_DIR / filename).read_bytes(), content_type)
        elif path == "/api/health":
            self._send_json({"status": "ok"})
        elif path == "/api/models":
            try:
                models = self.core.available_models()
            except requests.Timeout as exc:
                self._send_error_json(504, str(exc))
                return
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                self._send_error_json(502, str(exc))
                return
            settings = self.core.settings
            self._send_json(
                {
                    "models": models,
                    "defaults": {
                        "text": settings.text_model,
                        "code": settings.code_model,
                    },
                }
            )
        elif path == "/api/diagnostics":
            try:
                self._send_json(self.core.diagnostics())
            except (requests.RequestException, RuntimeError, ValueError, OSError):
                LOGGER.exception("Não foi possível montar o diagnóstico local.")
                self._send_error_json(500, "Não foi possível carregar o diagnóstico local.")
        elif path == "/api/voices":
            try:
                self._send_json(self.core.available_speech_voices())
            except requests.Timeout as exc:
                self._send_error_json(504, str(exc))
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                self._send_error_json(502, str(exc))
        elif path == "/api/conversations":
            try:
                self._send_json(
                    {"conversations": self.server.history_store.list_conversations()}
                )
            except sqlite3.Error:
                LOGGER.exception("Não foi possível listar as conversas locais.")
                self._send_error_json(500, "Não foi possível carregar as conversas locais.")
        elif path == "/api/gallery":
            try:
                self._send_json(
                    {"items": self.server.history_store.list_gallery_items()}
                )
            except (sqlite3.Error, json.JSONDecodeError):
                LOGGER.exception("Não foi possível carregar a galeria local.")
                self._send_error_json(500, "Não foi possível carregar a galeria local.")
        elif path == "/api/jobs":
            self._send_json({"jobs": self.server.job_queue.list_active()})
        elif path.startswith("/api/jobs/"):
            job_id = self._job_id_from_path(path)
            job = self.server.job_queue.get(job_id) if job_id else None
            if job is None:
                self._send_error_json(404, "Tarefa local não encontrada.")
            else:
                self._send_json({"job": job})
        elif path == "/api/history":
            try:
                conversation_id = self._query_conversation_id()
                if conversation_id is None:
                    return
                if not self.server.history_store.has_conversation(conversation_id):
                    self._send_error_json(404, "Conversa local não encontrada.")
                    return
                self._send_json(
                    {
                        "messages": self.server.history_store.list_messages(
                            conversation_id
                        )
                    }
                )
            except sqlite3.Error:
                LOGGER.exception("Não foi possível ler o histórico local.")
                self._send_error_json(500, "Não foi possível carregar o histórico local.")
        elif path.startswith("/api/images/"):
            self._serve_image(path.removeprefix("/api/images/"))
        elif path.startswith("/api/videos/"):
            self._serve_video(path.removeprefix("/api/videos/"))
        else:
            self._send_error_json(404, "Rota não encontrada.")

    def do_DELETE(self) -> None:
        if not self._validate_local_request(check_origin=True):
            return
        path = urlsplit(self.path).path
        if path == "/api/history":
            try:
                self.server.history_store.clear()
            except sqlite3.Error:
                LOGGER.exception("Não foi possível limpar o histórico local.")
                self._send_error_json(500, "Não foi possível limpar o histórico local.")
            else:
                self._send_json({"cleared": True})
            return
        job_id = self._job_id_from_path(path)
        if job_id is not None:
            job = self.server.job_queue.cancel(job_id)
            if job is None:
                self._send_error_json(404, "Tarefa local não encontrada.")
            else:
                self._send_json({"job": job})
            return
        conversation_id = self._conversation_id_from_path(path, "/api/conversations/")
        if conversation_id is None:
            self._send_error_json(404, "Rota não encontrada.")
            return
        try:
            conversation = self.server.history_store.delete_conversation(
                conversation_id
            )
        except sqlite3.Error:
            LOGGER.exception("Não foi possível excluir a conversa local.")
            self._send_error_json(500, "Não foi possível excluir a conversa local.")
        else:
            if conversation is None:
                self._send_error_json(404, "Conversa local não encontrada.")
            else:
                self._send_json({"deleted": True, "conversation": conversation})

    def do_POST(self) -> None:
        if not self._validate_local_request(check_origin=True):
            return
        path = urlsplit(self.path).path
        if path == "/api/conversations":
            self._create_conversation()
        elif path == "/api/chat":
            self._chat()
        elif path == "/api/chat/stream":
            self._chat_stream()
        elif path == "/api/transcribe":
            self._transcribe()
        elif path == "/api/speech":
            self._speech()
        else:
            self._send_error_json(404, "Rota não encontrada.")

    def do_PATCH(self) -> None:
        if not self._validate_local_request(check_origin=True):
            return
        path = urlsplit(self.path).path
        conversation_id = self._conversation_id_from_path(
            path, "/api/conversations/"
        )
        if conversation_id is None:
            self._send_error_json(404, "Rota não encontrada.")
            return
        payload = self._read_json(MAX_JSON_BYTES)
        if payload is None:
            return
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 72:
            self._send_error_json(400, "title deve conter de 1 a 72 caracteres.")
            return
        try:
            renamed = self.server.history_store.rename_conversation(
                conversation_id, " ".join(title.split())
            )
            conversation = (
                self.server.history_store.get_conversation(conversation_id)
                if renamed
                else None
            )
        except sqlite3.Error:
            LOGGER.exception("Não foi possível renomear a conversa local.")
            self._send_error_json(500, "Não foi possível renomear a conversa local.")
        else:
            if conversation is None:
                self._send_error_json(404, "Conversa local não encontrada.")
            else:
                self._send_json({"conversation": conversation})

    def _create_conversation(self) -> None:
        payload = self._read_json(MAX_JSON_BYTES)
        if payload is None:
            return
        title = payload.get("title", "Nova conversa")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 72:
            self._send_error_json(400, "title deve conter de 1 a 72 caracteres.")
            return
        try:
            conversation = self.server.history_store.create_conversation(
                " ".join(title.split())
            )
        except sqlite3.Error:
            LOGGER.exception("Não foi possível criar a conversa local.")
            self._send_error_json(500, "Não foi possível criar a conversa local.")
        else:
            self._send_json({"conversation": conversation}, status=201)

    def _query_conversation_id(self) -> int | None:
        values = parse_qs(urlsplit(self.path).query).get("conversation_id")
        if values is None:
            conversations = self.server.history_store.list_conversations()
            return int(conversations[0]["id"]) if conversations else None
        if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
            self._send_error_json(400, "conversation_id deve ser um inteiro positivo.")
            return None
        return int(values[0])

    @staticmethod
    def _conversation_id_from_path(path: str, prefix: str) -> int | None:
        suffix = path.removeprefix(prefix)
        if path == prefix or not suffix.isdigit() or int(suffix) < 1:
            return None
        return int(suffix)

    @staticmethod
    def _job_id_from_path(path: str) -> str | None:
        suffix = path.removeprefix("/api/jobs/")
        if path == "/api/jobs/" or not re.fullmatch(r"[a-f0-9]{32}", suffix):
            return None
        return suffix

    def _payload_conversation_id(self, payload: dict[str, Any]) -> int | None:
        if "conversation_id" not in payload:
            return None
        conversation_id = payload["conversation_id"]
        if (
            not isinstance(conversation_id, int)
            or isinstance(conversation_id, bool)
            or conversation_id < 1
        ):
            self._send_error_json(400, "conversation_id deve ser um inteiro positivo.")
            return None
        if not self.server.history_store.has_conversation(conversation_id):
            self._send_error_json(404, "Conversa local não encontrada.")
            return None
        return conversation_id

    def _chat(self) -> None:
        payload = self._read_json(MAX_JSON_BYTES)
        if payload is None:
            return
        task = payload.get("task", "text")
        model = payload.get("model")
        conversation_id = self._payload_conversation_id(payload)
        if "conversation_id" in payload and conversation_id is None:
            return
        generate_image = payload.get("generate_image", False)
        generate_video = payload.get("generate_video", False)
        messages = payload.get("messages")
        if task not in {"text", "code"}:
            self._send_error_json(400, "task deve ser 'text' ou 'code'.")
            return
        if model is not None and not isinstance(model, str):
            self._send_error_json(400, "model deve ser texto ou nulo.")
            return
        if not isinstance(generate_image, bool):
            self._send_error_json(400, "generate_image deve ser verdadeiro ou falso.")
            return
        if not isinstance(generate_video, bool):
            self._send_error_json(400, "generate_video deve ser verdadeiro ou falso.")
            return
        if generate_image and generate_video:
            self._send_error_json(400, "Escolha geração de imagem ou de vídeo, não ambas.")
            return
        if not isinstance(messages, list):
            self._send_error_json(400, "messages deve ser uma lista.")
            return
        if self.core.is_media_request(messages, generate_image, generate_video):
            last_message = messages[-1] if messages else None
            if not isinstance(last_message, dict) or not isinstance(
                last_message.get("content"), str
            ):
                self._send_error_json(400, "A última mensagem precisa conter texto.")
                return

            def execute_media(
                report_progress: Callable[[str], None],
                cancelled: Callable[[], bool],
            ) -> dict[str, object]:
                result = self.core.chat(
                    messages,
                    task=task,
                    model=model,
                    generate_image=generate_image,
                    generate_video=generate_video,
                    progress=report_progress,
                    cancelled=cancelled,
                )
                self.server.history_store.append_turn(
                    last_message["content"], result, conversation_id
                )
                return result

            try:
                job = self.server.job_queue.submit(execute_media)
            except OverflowError as exc:
                self._send_error_json(429, str(exc))
            else:
                self._send_json({"job": job}, status=202)
            return
        try:
            result = self.core.chat(
                messages,
                task=task,
                model=model,
                generate_image=generate_image,
                generate_video=generate_video,
            )
        except ValueError as exc:
            LOGGER.warning("Requisição de chat inválida: %s", exc)
            self._send_error_json(400, str(exc))
        except (TimeoutError, requests.Timeout) as exc:
            LOGGER.error("Tempo limite ao processar chat local: %s", exc)
            self._send_error_json(504, str(exc))
        except (requests.RequestException, RuntimeError, OSError) as exc:
            LOGGER.error("Falha ao processar chat local: %s", exc)
            self._send_error_json(502, str(exc))
        else:
            if result.get("image_error"):
                LOGGER.warning("Falha ao gerar imagem: %s", result["image_error"])
            if result.get("video_error"):
                LOGGER.warning("Falha ao gerar vídeo: %s", result["video_error"])
            try:
                last_message = messages[-1]
                if not isinstance(last_message, dict) or not isinstance(
                    last_message.get("content"), str
                ):
                    raise ValueError("A última mensagem precisa conter texto.")
                self.server.history_store.append_turn(
                    last_message["content"], result, conversation_id
                )
            except (sqlite3.Error, ValueError, KeyError, TypeError):
                LOGGER.exception("A resposta foi gerada, mas não foi salva no histórico.")
                self._send_error_json(
                    500, "A resposta foi gerada, mas não foi salva no histórico local."
                )
            else:
                self._send_json(result)

    def _chat_stream(self) -> None:
        payload = self._read_json(MAX_JSON_BYTES)
        if payload is None:
            return
        task = payload.get("task", "text")
        model = payload.get("model")
        conversation_id = self._payload_conversation_id(payload)
        if "conversation_id" in payload and conversation_id is None:
            return
        messages = payload.get("messages")
        if task not in {"text", "code"}:
            self._send_error_json(400, "task deve ser 'text' ou 'code'.")
            return
        if model is not None and not isinstance(model, str):
            self._send_error_json(400, "model deve ser texto ou nulo.")
            return
        if not isinstance(messages, list):
            self._send_error_json(400, "messages deve ser uma lista.")
            return

        try:
            selected_model, tokens = self.core.stream_chat(messages, task, model)
        except ValueError as exc:
            LOGGER.warning("Requisição de streaming inválida: %s", exc)
            self._send_error_json(400, str(exc))
            return
        except (TimeoutError, requests.Timeout) as exc:
            LOGGER.error("Tempo limite ao preparar streaming local: %s", exc)
            self._send_error_json(504, str(exc))
            return
        except (requests.RequestException, RuntimeError, OSError) as exc:
            LOGGER.error("Falha ao preparar streaming local: %s", exc)
            self._send_error_json(502, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()

        answer_parts: list[str] = []
        try:
            for token in tokens:
                if not isinstance(token, str):
                    raise RuntimeError("O modelo retornou um fragmento de texto inválido.")
                answer_parts.append(token)
                self._send_stream_event({"type": "token", "token": token})

            answer = "".join(answer_parts)
            if not answer.strip():
                raise RuntimeError("O modelo local retornou uma resposta vazia.")
            result: dict[str, object] = {
                "answer": answer,
                "model": selected_model,
                "task": task,
                "image_url": None,
                "video_url": None,
            }
            last_message = messages[-1]
            if not isinstance(last_message, dict) or not isinstance(
                last_message.get("content"), str
            ):
                raise ValueError("A última mensagem precisa conter texto.")
            self.server.history_store.append_turn(
                last_message["content"], result, conversation_id
            )
            self._send_stream_event({"type": "done", "result": result})
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info("Cliente encerrou o streaming antes da resposta completa.")
        except ValueError as exc:
            LOGGER.warning("Requisição de streaming inválida: %s", exc)
            self._send_stream_error(str(exc))
        except (TimeoutError, requests.Timeout) as exc:
            LOGGER.error("Tempo limite durante streaming local: %s", exc)
            self._send_stream_error(str(exc))
        except (requests.RequestException, RuntimeError, OSError, sqlite3.Error) as exc:
            LOGGER.exception("Falha durante streaming local.")
            self._send_stream_error(str(exc))
        finally:
            close = getattr(tokens, "close", None)
            if callable(close):
                close()
            self.close_connection = True

    def _send_stream_error(self, message: str) -> None:
        try:
            self._send_stream_event({"type": "error", "error": message})
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info("Cliente encerrou a conexão ao receber erro de streaming.")
        except OSError:
            LOGGER.exception("Não foi possível enviar o erro pelo streaming.")

    def _send_stream_event(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.wfile.write(data)
        self.wfile.flush()

    def _transcribe(self) -> None:
        length = self._content_length(MAX_AUDIO_BYTES)
        if length is None:
            return
        filename = self.headers.get("X-Audio-Filename", "")
        if not filename or Path(filename).name != filename:
            self._send_error_json(400, "X-Audio-Filename deve conter apenas um nome.")
            return
        content_type = self.headers.get_content_type()
        if not content_type.startswith("audio/") and content_type not in {
            "application/octet-stream",
            "video/webm",
        }:
            self._send_error_json(415, "O corpo precisa ser um arquivo de áudio.")
            return
        try:
            audio = self.rfile.read(length)
            if len(audio) != length:
                self._send_error_json(400, "O envio do áudio foi interrompido.")
                return
            result = self.core.transcribe(audio, filename)
        except ValueError as exc:
            LOGGER.warning("Requisição de transcrição inválida: %s", exc)
            self._send_error_json(400, str(exc))
        except (TimeoutError, requests.Timeout) as exc:
            LOGGER.error("Tempo limite ao transcrever áudio local: %s", exc)
            self._send_error_json(504, str(exc))
        except (requests.RequestException, RuntimeError, OSError) as exc:
            LOGGER.error("Falha na transcrição local: %s", exc)
            self._send_error_json(502, str(exc))
        else:
            self._send_json(result)

    def _speech(self) -> None:
        payload = self._read_json(MAX_JSON_BYTES)
        if payload is None:
            return
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_error_json(400, "text deve ser uma string.")
            return
        voice_id = payload.get("voice_id")
        if voice_id is not None and not isinstance(voice_id, str):
            self._send_error_json(400, "voice_id deve ser texto ou nulo.")
            return
        try:
            audio = self.core.synthesize_speech(text, voice_id)
        except ValueError as exc:
            LOGGER.warning("Requisição de síntese inválida: %s", exc)
            self._send_error_json(400, str(exc))
        except (TimeoutError, requests.Timeout) as exc:
            LOGGER.error("Tempo limite na síntese local: %s", exc)
            self._send_error_json(504, str(exc))
        except (requests.RequestException, RuntimeError, OSError) as exc:
            LOGGER.error("Falha na síntese local: %s", exc)
            self._send_error_json(502, str(exc))
        else:
            self._send_bytes(audio, "audio/wav")

    def _serve_image(self, filename: str) -> None:
        if not SAFE_IMAGE_NAME.fullmatch(filename):
            self._send_error_json(404, "Imagem não encontrada.")
            return
        image_root = (OUTPUT_DIR / "images").resolve()
        image_path = (image_root / filename).resolve()
        if not image_path.is_relative_to(image_root) or not image_path.is_file():
            self._send_error_json(404, "Imagem não encontrada.")
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_bytes(image_path.read_bytes(), content_type)

    def _serve_video(self, filename: str) -> None:
        if not SAFE_IMAGE_NAME.fullmatch(filename) or not filename.endswith(".webm"):
            self._send_error_json(404, "Vídeo não encontrado.")
            return
        video_root = (OUTPUT_DIR / "videos").resolve()
        video_path = (video_root / filename).resolve()
        if not video_path.is_relative_to(video_root) or not video_path.is_file():
            self._send_error_json(404, "Vídeo não encontrado.")
            return
        self._send_bytes(video_path.read_bytes(), "video/webm")

    def _read_json(self, limit: int) -> dict[str, Any] | None:
        length = self._content_length(limit)
        if length is None:
            return None
        if self.headers.get_content_type() != "application/json":
            self._send_error_json(415, "Use Content-Type application/json.")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error_json(400, "O corpo da requisição não contém JSON válido.")
            return None
        if not isinstance(payload, dict):
            self._send_error_json(400, "O corpo JSON deve ser um objeto.")
            return None
        return payload

    def _content_length(self, limit: int) -> int | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_error_json(411, "Content-Length válido é obrigatório.")
            return None
        if length < 0:
            self._send_error_json(400, "Content-Length inválido.")
            return None
        if length > limit:
            self._send_error_json(413, "O corpo da requisição excede o limite permitido.")
            return None
        return length

    def _validate_local_request(self, check_origin: bool = False) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed_host = urlsplit(f"//{host}")
            hostname = parsed_host.hostname
            port = parsed_host.port
            loopback = hostname == "localhost" or (
                hostname is not None and ipaddress.ip_address(hostname).is_loopback
            )
        except ValueError:
            loopback = False
            port = None
        if not loopback or port != self.server.server_port:
            self._send_error_json(403, "A API aceita apenas conexões locais.")
            return False
        if check_origin:
            origin = self.headers.get("Origin")
            if origin:
                parsed_origin = urlsplit(origin)
                if (
                    parsed_origin.scheme != "http"
                    or parsed_origin.netloc.lower() != host.lower()
                ):
                    self._send_error_json(403, "Origem não permitida.")
                    return False
        return True

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_error_json(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    history_path: Path | None = None,
) -> ThreadingHTTPServer:
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise ValueError("O backend só pode escutar em localhost/loopback.")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("A porta do backend deve estar entre 0 e 65535.")
    server = AgentHTTPServer((host, port), AgentRequestHandler, history_path)
    server.daemon_threads = True
    return server


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOGGER.handlers:
        return
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    file_handler = RotatingFileHandler(
        LOG_DIR / "agent-service.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(console_handler)
    LOGGER.propagate = False
    job_logger = logging.getLogger("agents.jobs")
    job_logger.setLevel(logging.INFO)
    job_logger.addHandler(file_handler)
    job_logger.addHandler(console_handler)
    job_logger.propagate = False
    forwarder_logger = logging.getLogger("agents.port_forwarder")
    forwarder_logger.setLevel(logging.INFO)
    forwarder_logger.addHandler(file_handler)
    forwarder_logger.addHandler(console_handler)
    forwarder_logger.propagate = False


def main() -> int:
    import os

    configure_logging()
    host = os.getenv("AGENT_HOST", "127.0.0.1")
    port_value = os.getenv("AGENT_PORT", "8765")
    forwarder = LocalPortForwarder()
    server: ThreadingHTTPServer | None = None
    try:
        forwarder.start()
        port = int(port_value)
        if not 1 <= port <= 65535:
            raise ValueError("AGENT_PORT deve estar entre 1 e 65535.")
        server = create_server(host, port)
    except (ValueError, OSError) as exc:
        LOGGER.error("Não foi possível iniciar o serviço local: %s", exc)
        forwarder.close()
        return 1
    except RuntimeError as exc:
        LOGGER.error("Não foi possível preparar os serviços locais: %s", exc)
        forwarder.close()
        return 1
    LOGGER.info("Interface local: http://%s:%s", host, server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Encerrando o serviço local.")
    finally:
        server.server_close()
        forwarder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
