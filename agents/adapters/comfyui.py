from __future__ import annotations

import json
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from agents.settings import Settings

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "outputs"


class ComfyUICommandError(RuntimeError):
    pass


class ComfyUIAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def system_stats(self) -> dict[str, object]:
        try:
            return self.request("GET", "/system_stats", timeout=3)
        except ComfyUICommandError as exc:
            raise ConnectionError(f"ComfyUI indisponível: {exc}") from exc

    def interrupt(self) -> None:
        self.request("POST", "/interrupt", timeout=5)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout: int | None = None,
    ) -> dict[str, object]:
        request_timeout = (
            self.settings.comfyui_request_timeout_seconds
            if timeout is None
            else timeout
        )
        script = (
            "import sys, urllib.request\n"
            "class NoRedirect(urllib.request.HTTPRedirectHandler):\n"
            "    def redirect_request(self, req, fp, code, msg, headers, newurl):\n"
            "        return None\n"
            "method, path, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])\n"
            "body = sys.stdin.buffer.read()\n"
            "request = urllib.request.Request(\n"
            "    sys.argv[4] + path,\n"
            "    data=body if body else None,\n"
            "    headers={'Content-Type': 'application/json'} if body else {},\n"
            "    method=method,\n"
            ")\n"
            "opener = urllib.request.build_opener(NoRedirect)\n"
            "with opener.open(request, timeout=timeout) as response:\n"
            "    sys.stdout.write(response.read().decode('utf-8'))\n"
        )
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    self.settings.comfyui_container,
                    "python3",
                    "-c",
                    script,
                    method,
                    path,
                    str(request_timeout),
                    self.settings.comfyui_api_url,
                ],
                input=json.dumps(payload) if payload is not None else "",
                capture_output=True,
                text=True,
                timeout=request_timeout + 10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Tempo limite ao chamar ComfyUI: {path}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ComfyUICommandError(f"Falha ao chamar ComfyUI: {detail}")
        try:
            result: Any = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ComfyUI retornou JSON inválido.") from exc
        if not isinstance(result, dict):
            raise RuntimeError("ComfyUI retornou um formato JSON inesperado.")
        return result

    def create_image(
        self,
        prompt: str,
        run_id: str,
        output_dir: Path | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        workflow = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.settings.checkpoint},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512, "batch_size": 1},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": "low quality, blurry, text, watermark, deformed",
                    "clip": ["4", 1],
                },
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": secrets.randbelow(2**32),
                    "steps": 20,
                    "cfg": 7,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"agent_{run_id}",
                    "images": ["8", 0],
                },
            },
        }
        if cancelled is not None and cancelled():
            raise InterruptedError("Geração de imagem cancelada.")
        if progress is not None:
            progress("Enviando workflow de imagem para o ComfyUI.")
        queue_result = self.request("POST", "/prompt", {"prompt": workflow})
        prompt_id = queue_result.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError("ComfyUI não retornou um ID de execução.")

        deadline = time.monotonic() + self.settings.image_timeout_seconds
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                self.interrupt()
                raise InterruptedError("Geração de imagem cancelada.")
            if progress is not None:
                progress("Gerando imagem no ComfyUI.")
            result = self.request("GET", f"/history/{prompt_id}", timeout=30)
            history = result.get(prompt_id)
            if isinstance(history, dict):
                status = history.get("status", {})
                if isinstance(status, dict) and status.get("completed"):
                    outputs = history.get("outputs", {})
                    save_result = outputs.get("9", {}) if isinstance(outputs, dict) else {}
                    images = save_result.get("images", []) if isinstance(save_result, dict) else []
                    if not images or not isinstance(images[0], dict):
                        raise RuntimeError(
                            "ComfyUI concluiu sem retornar uma imagem."
                        )
                    image_info = images[0]
                    filename = image_info.get("filename")
                    subfolder = image_info.get("subfolder", "")
                    if not isinstance(filename, str) or Path(filename).name != filename:
                        raise RuntimeError("ComfyUI retornou nome de arquivo inválido.")
                    image_root = (
                        OUTPUT_DIR if output_dir is None else output_dir
                    ) / "images"
                    image_root = image_root.resolve()
                    image_path = (image_root / subfolder / filename).resolve()
                    if not image_path.is_relative_to(image_root):
                        raise RuntimeError("ComfyUI retornou caminho fora da pasta de imagens.")
                    if image_path.is_file():
                        return image_path
                    raise RuntimeError("A imagem concluída não apareceu na pasta de saída.")
                if isinstance(status, dict) and status.get("status_str") == "error":
                    raise RuntimeError(
                        f"ComfyUI falhou ao gerar a imagem: {status.get('messages', [])}"
                    )
            time.sleep(2)
        raise TimeoutError(
            f"ComfyUI não concluiu a geração em "
            f"{self.settings.image_timeout_seconds} segundos."
        )
