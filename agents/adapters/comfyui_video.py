from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Protocol

from agents.adapters.comfyui import OUTPUT_DIR, ComfyUIAdapter
from agents.settings import Settings

PROJECT_DIR = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_DIR / "stable-video" / "wan2.1_t2v_1.3B_pilot.json"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class ComfyUIRequestPort(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout: int | None = None,
    ) -> dict[str, object]: ...

    def interrupt(self) -> None: ...


class ComfyUIVideoAdapter:
    def __init__(
        self,
        settings: Settings,
        comfyui: ComfyUIRequestPort | None = None,
    ) -> None:
        self.settings = settings
        self.comfyui = comfyui if comfyui is not None else ComfyUIAdapter(settings)

    def interrupt(self) -> None:
        self.comfyui.request("POST", "/interrupt", timeout=5)

    def create_video(
        self,
        prompt: str,
        run_id: str,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("O identificador do vídeo contém caracteres inválidos.")

        try:
            payload = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("O workflow local do Wan não contém JSON válido.") from exc
        workflow = payload.get("prompt") if isinstance(payload, dict) else None
        if not isinstance(workflow, dict):
            raise RuntimeError("O workflow local do Wan não contém um grafo de prompt.")
        prompt_node = workflow.get("4")
        save_node = workflow.get("9")
        if not isinstance(prompt_node, dict) or not isinstance(save_node, dict):
            raise RuntimeError("O workflow local do Wan não contém os nodes esperados.")
        prompt_inputs = prompt_node.get("inputs")
        save_inputs = save_node.get("inputs")
        if not isinstance(prompt_inputs, dict) or not isinstance(save_inputs, dict):
            raise RuntimeError("O workflow local do Wan tem entradas inválidas.")
        prompt_inputs["text"] = prompt
        save_inputs["filename_prefix"] = f"agent_{run_id}"

        if cancelled is not None and cancelled():
            raise InterruptedError("Geração de vídeo cancelada.")
        if progress is not None:
            progress("Enviando workflow de vídeo para o ComfyUI.")
        queued = self.comfyui.request("POST", "/prompt", payload)
        prompt_id = queued.get("prompt_id")
        if queued.get("node_errors") or not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError(f"ComfyUI rejeitou o workflow de vídeo: {queued}")

        deadline = time.monotonic() + self.settings.video_timeout_seconds
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                self.interrupt()
                raise InterruptedError("Geração de vídeo cancelada.")
            if progress is not None:
                progress("Gerando vídeo no ComfyUI.")
            history = self.comfyui.request("GET", f"/history/{prompt_id}", timeout=30)
            result = history.get(prompt_id)
            if isinstance(result, dict):
                status = result.get("status")
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise RuntimeError(
                        f"ComfyUI falhou ao gerar o vídeo: {status.get('messages', [])}"
                    )
                if isinstance(status, dict) and status.get("completed"):
                    return self._move_video(result)
            time.sleep(5)
        raise TimeoutError(
            f"ComfyUI não concluiu o vídeo em "
            f"{self.settings.video_timeout_seconds} segundos."
        )

    @staticmethod
    def _move_video(history: dict[str, object]) -> Path:
        outputs = history.get("outputs")
        save_result = outputs.get("9") if isinstance(outputs, dict) else None
        videos = save_result.get("images") if isinstance(save_result, dict) else None
        if not isinstance(videos, list) or not videos or not isinstance(videos[0], dict):
            raise RuntimeError("ComfyUI concluiu sem retornar um arquivo de vídeo.")
        video_info = videos[0]
        filename = video_info.get("filename")
        subfolder = video_info.get("subfolder", "")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".webm")
            or not isinstance(subfolder, str)
        ):
            raise RuntimeError("ComfyUI retornou informações de vídeo inválidas.")
        if video_info.get("type") != "output":
            raise RuntimeError("ComfyUI retornou um vídeo que não é de saída.")

        image_root = (OUTPUT_DIR / "images").resolve()
        video_path = (image_root / subfolder / filename).resolve()
        if not video_path.is_relative_to(image_root) or not video_path.is_file():
            raise RuntimeError("O arquivo de vídeo não apareceu na pasta de saída.")

        video_root = (OUTPUT_DIR / "videos").resolve()
        video_root.mkdir(parents=True, exist_ok=True)
        destination = (video_root / filename).resolve()
        if not destination.is_relative_to(video_root):
            raise RuntimeError("ComfyUI retornou um caminho de vídeo inválido.")
        if destination.exists():
            raise RuntimeError(f"O arquivo de vídeo já existe: {destination.name}")
        video_path.rename(destination)
        return destination
