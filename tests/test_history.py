from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agents.history import HistoryStore


class HistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "history.sqlite3"
        self.store = HistoryStore(self.path)

    def test_keeps_conversation_messages_separate_and_titles_first_turn(self) -> None:
        first_id = int(self.store.list_conversations()[0]["id"])
        self.store.append_turn(
            "  Como   funciona o   Ollama? ",
            {"answer": "Resposta", "model": "qwen3:4b", "task": "text"},
            first_id,
        )
        second = self.store.create_conversation()
        second_id = int(second["id"])
        self.store.append_turn(
            "Outra pergunta",
            {"answer": "Outra resposta"},
            second_id,
        )

        self.assertEqual(self.store.list_messages(first_id)[0]["content"], "  Como   funciona o   Ollama? ")
        self.assertEqual(self.store.list_messages(second_id)[0]["content"], "Outra pergunta")
        self.assertEqual(self.store.get_conversation(first_id)["title"], "Como funciona o Ollama?")
        self.assertEqual(self.store.get_conversation(second_id)["title"], "Outra pergunta")

    def test_renames_and_deletes_conversation_without_losing_others(self) -> None:
        first_id = int(self.store.list_conversations()[0]["id"])
        second = self.store.create_conversation("Secundária")
        second_id = int(second["id"])
        self.store.append_turn("Conservar", {"answer": "Sim"}, first_id)

        self.assertTrue(self.store.rename_conversation(second_id, "Renomeada"))
        remaining = self.store.delete_conversation(first_id)

        self.assertEqual(remaining["id"], second_id)
        self.assertEqual(remaining["title"], "Renomeada")
        self.assertEqual(len(self.store.list_conversations()), 1)

    def test_deleting_last_conversation_creates_an_empty_replacement(self) -> None:
        conversation_id = int(self.store.list_conversations()[0]["id"])

        replacement = self.store.delete_conversation(conversation_id)

        self.assertNotEqual(replacement["id"], conversation_id)
        self.assertEqual(replacement["title"], "Nova conversa")
        self.assertEqual(self.store.list_messages(replacement["id"]), [])

    def test_migrates_existing_single_history_without_losing_messages(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO messages (role, content, metadata, created_at)
                VALUES (?, ?, '{}', '2026-01-01T00:00:00+00:00')
                """,
                [("user", "Antiga pergunta"), ("assistant", "Antiga resposta")],
            )

        migrated = HistoryStore(legacy_path)
        conversations = migrated.list_conversations()
        messages = migrated.list_messages(int(conversations[0]["id"]))

        self.assertEqual(conversations[0]["title"], "Conversa anterior")
        self.assertEqual(
            [message["content"] for message in messages],
            ["Antiga pergunta", "Antiga resposta"],
        )

    def test_clear_resets_messages_and_keeps_a_new_conversation(self) -> None:
        conversation_id = int(self.store.list_conversations()[0]["id"])
        self.store.append_turn("Pergunta", {"answer": "Resposta"}, conversation_id)

        self.store.clear()

        conversations = self.store.list_conversations()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["title"], "Nova conversa")
        self.assertEqual(self.store.list_messages(int(conversations[0]["id"])), [])

    def test_gallery_includes_only_safe_local_media_with_their_prompt(self) -> None:
        conversation_id = int(self.store.list_conversations()[0]["id"])
        self.store.append_turn(
            "Uma paisagem",
            {
                "answer": "Concluído",
                "image_url": "/api/images/paisagem.png",
                "video_url": "/api/videos/paisagem.webm",
            },
            conversation_id,
        )
        self.store.append_turn(
            "Referências inválidas",
            {
                "answer": "Não incluir",
                "image_url": "https://example.com/image.png",
                "video_url": "/api/videos/../outside.webm",
            },
            conversation_id,
        )

        items = self.store.list_gallery_items()

        self.assertEqual(
            [(item["type"], item["url"]) for item in items],
            [
                ("video", "/api/videos/paisagem.webm"),
                ("image", "/api/images/paisagem.png"),
            ],
        )
        self.assertEqual(items[0]["prompt"], "Uma paisagem")
        self.assertEqual(items[0]["conversation_title"], "Uma paisagem")


if __name__ == "__main__":
    unittest.main()
