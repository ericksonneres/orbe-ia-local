from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agents.adapters.comfyui_video as video_module
from agents.adapters.comfyui_video import ComfyUIVideoAdapter
from agents.settings import Settings


class FakeComfyUI:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.timeouts: list[int | None] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout: int | None = None,
    ) -> dict[str, object]:
        self.calls.append((method, path, payload))
        self.timeouts.append(timeout)
        if method == "POST":
            return {"prompt_id": "pilot"}
        video_path = self.output_dir / "images" / "agent_test_run_00001_.webm"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        return {
            "pilot": {
                "status": {"completed": True},
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": video_path.name,
                                "subfolder": "",
                                "type": "output",
                                "metadata": {"kind": "video"},
                            }
                        ]
                    }
                },
            }
        }


class ComfyUIVideoAdapterTests(unittest.TestCase):
    def test_submits_workflow_and_moves_video_to_video_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir) / "outputs"
            comfyui = FakeComfyUI(output_dir)
            adapter = ComfyUIVideoAdapter(Settings(video_timeout_seconds=10), comfyui)
            with patch.object(video_module, "OUTPUT_DIR", output_dir):
                video_path = adapter.create_video("A calm local test prompt", "test_run")

            self.assertEqual(
                video_path,
                output_dir / "videos" / "agent_test_run_00001_.webm",
            )
            self.assertEqual(video_path.read_bytes(), b"video")
            self.assertFalse((output_dir / "images" / video_path.name).exists())
            payload = comfyui.calls[0][2]
            self.assertIsNotNone(payload)
            prompt = payload["prompt"]
            self.assertEqual(prompt["4"]["inputs"]["text"], "A calm local test prompt")
            self.assertEqual(prompt["9"]["inputs"]["filename_prefix"], "agent_test_run")
            saved_workflow = json.loads(video_module.WORKFLOW_PATH.read_text())
            self.assertEqual(saved_workflow["prompt"]["6"]["inputs"]["length"], 33)
            self.assertEqual([call[0] for call in comfyui.calls], ["POST", "GET"])

    def test_interrupts_video_generation_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            adapter = ComfyUIVideoAdapter(
                Settings(video_timeout_seconds=10),
                FakeComfyUI(Path(temporary_dir)),
            )
            checks = 0

            def cancelled() -> bool:
                nonlocal checks
                checks += 1
                return checks > 1

            with self.assertRaisesRegex(InterruptedError, "cancelada"):
                adapter.create_video(
                    "A cancellable test prompt",
                    "cancelled_run",
                    cancelled=cancelled,
                )

            self.assertEqual(
                [(method, path) for method, path, _payload in adapter.comfyui.calls],
                [("POST", "/prompt"), ("POST", "/interrupt")],
            )


if __name__ == "__main__":
    unittest.main()
