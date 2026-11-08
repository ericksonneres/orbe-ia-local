from __future__ import annotations

import io
import unittest
import wave
from unittest.mock import Mock, patch

from agents.adapters.speech import PiperSpeechAdapter
from agents.settings import Settings


class PiperSpeechAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.adapter = PiperSpeechAdapter(self.settings)

    def test_requests_local_piper_and_returns_wav(self) -> None:
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 5)
        response = Mock(status_code=200, content=audio_buffer.getvalue())
        response.raise_for_status.return_value = None

        with patch(
            "agents.adapters.speech.requests.post", return_value=response
        ) as post:
            result = self.adapter.synthesize("Olá")

        self.assertEqual(result, response.content)
        post.assert_called_once_with(
            "http://127.0.0.1:8001/synthesize",
            json={"text": "Olá"},
            timeout=(15, 120),
            allow_redirects=False,
        )

    def test_sends_selected_voice_id(self) -> None:
        response = Mock(status_code=200, content=b"RIFF\x00\x00\x00\x00WAVE")
        response.raise_for_status.return_value = None
        with patch("agents.adapters.speech.requests.post", return_value=response) as post:
            self.adapter.synthesize("Hello", "en_US-hfc_female-medium")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"text": "Hello", "voice_id": "en_US-hfc_female-medium"},
        )

    def test_reads_voice_catalog_from_local_service(self) -> None:
        catalog = {"default": "pt_BR-faber-medium", "voices": []}
        response = Mock(status_code=200)
        response.json.return_value = catalog
        response.raise_for_status.return_value = None
        with patch("agents.adapters.speech.requests.get", return_value=response) as get:
            self.assertEqual(self.adapter.available_voices(), catalog)
        self.assertEqual(
            get.call_args.args[0],
            "http://127.0.0.1:8001/voices",
        )

    def test_rejects_empty_or_oversized_text(self) -> None:
        for text in ("  ", "x" * 4001):
            with self.subTest(length=len(text)):
                with self.assertRaises(ValueError):
                    self.adapter.synthesize(text)

    def test_reports_invalid_wav_from_piper_service(self) -> None:
        response = Mock(status_code=200, content=b"not a wave")
        response.raise_for_status.return_value = None
        with patch("agents.adapters.speech.requests.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "WAV inválido"):
                self.adapter.synthesize("Olá")

    def test_does_not_follow_redirects_from_local_service(self) -> None:
        response = Mock(status_code=302, content=b"")
        with patch(
            "agents.adapters.speech.requests.post", return_value=response
        ) as post:
            with self.assertRaisesRegex(RuntimeError, "redirecionamento"):
                self.adapter.synthesize("Olá")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_reports_invalid_voice_as_bad_input(self) -> None:
        response = Mock(status_code=400)
        response.json.return_value = {"detail": "Voz Piper desconhecida."}
        with patch("agents.adapters.speech.requests.post", return_value=response):
            with self.assertRaisesRegex(ValueError, "Voz Piper desconhecida"):
                self.adapter.synthesize("Olá", "unknown")


if __name__ == "__main__":
    unittest.main()
