from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from agents.adapters.ollama import OllamaAdapter
from agents.settings import Settings


class OllamaStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.adapter = OllamaAdapter(self.settings)

    def test_chat_stream_yields_tokens_and_closes_response(self) -> None:
        response = Mock(status_code=200)
        response.iter_lines.return_value = [
            b'{"message":{"content":"Ola"},"done":false}',
            b'{"message":{"content":" mundo"},"done":false}',
            b'{"message":{"content":""},"done":true}',
        ]
        with patch(
            "agents.adapters.ollama.requests.post", return_value=response
        ) as post:
            tokens = list(
                self.adapter.chat_stream(
                    [{"role": "user", "content": "Oi"}],
                    "qwen3:4b",
                )
            )

        self.assertEqual(tokens, ["Ola", " mundo"])
        response.iter_lines.assert_called_once_with(
            chunk_size=1,
            decode_unicode=True,
        )
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "qwen3:4b",
                "messages": [{"role": "user", "content": "Oi"}],
                "stream": True,
                "options": {"num_ctx": 4096, "temperature": 0.4},
            },
        )
        response.close.assert_called_once()

    def test_chat_stream_surfaces_ollama_error(self) -> None:
        response = Mock(status_code=200)
        response.iter_lines.return_value = [
            b'{"error":"modelo indisponivel"}',
        ]
        with patch(
            "agents.adapters.ollama.requests.post", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "modelo indisponivel"):
                list(self.adapter.chat_stream([], "qwen3:4b"))
        response.close.assert_called_once()

    def test_chat_stream_rejects_response_without_done_marker(self) -> None:
        response = Mock(status_code=200)
        response.iter_lines.return_value = [
            b'{"message":{"content":"parcial"},"done":false}',
        ]
        with patch(
            "agents.adapters.ollama.requests.post", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "sem concluir"):
                list(self.adapter.chat_stream([], "qwen3:4b"))
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
