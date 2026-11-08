from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

GALLERY_MEDIA_URL = re.compile(
    r"/api/(?P<kind>images|videos)/(?P<filename>[A-Za-z0-9._-]+)\Z"
)


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            message_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(messages)")
            }
            if not message_columns:
                connection.execute(
                    """
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id INTEGER NOT NULL
                            REFERENCES conversations(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
            elif "conversation_id" not in message_columns:
                connection.execute(
                    """
                    ALTER TABLE messages ADD COLUMN conversation_id INTEGER
                        REFERENCES conversations(id) ON DELETE CASCADE
                    """
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS messages_by_conversation "
                "ON messages(conversation_id, id)"
            )
            has_conversation = connection.execute(
                "SELECT 1 FROM conversations LIMIT 1"
            ).fetchone()
            if not has_conversation:
                has_legacy_messages = connection.execute(
                    "SELECT 1 FROM messages LIMIT 1"
                ).fetchone()
                title = "Conversa anterior" if has_legacy_messages else "Nova conversa"
                self._insert_conversation(connection, title)
            default_id = connection.execute(
                "SELECT id FROM conversations ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            connection.execute(
                "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL",
                (default_id,),
            )

    def list_conversations(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id)
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row[4],
            }
            for row in rows
        ]

    def create_conversation(self, title: str = "Nova conversa") -> dict[str, object]:
        with self._connect() as connection:
            conversation_id = self._insert_conversation(connection, title)
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeError("A conversa criada não pôde ser recuperada.")
        return conversation

    def get_conversation(self, conversation_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id)
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row[4],
        }

    def has_conversation(self, conversation_id: int) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone() is not None

    def rename_conversation(self, conversation_id: int, title: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
        return cursor.rowcount == 1

    def delete_conversation(self, conversation_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            if cursor.rowcount != 1:
                return None
            remaining = connection.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if remaining is None:
                self._insert_conversation(connection, "Nova conversa")
        latest = self.list_conversations()[0]
        return latest

    def append_turn(
        self,
        user_content: str,
        result: dict[str, object],
        conversation_id: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        assistant_metadata = {
            key: result.get(key)
            for key in (
                "model",
                "task",
                "image_url",
                "image_error",
                "video_url",
                "video_error",
            )
            if result.get(key) is not None
        }
        title = " ".join(user_content.split())[:72] or "Nova conversa"
        with self._connect() as connection:
            if conversation_id is None:
                row = connection.execute(
                    "SELECT id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    conversation_id = self._insert_conversation(
                        connection, "Nova conversa"
                    )
                else:
                    conversation_id = row["id"]
            exists = connection.execute(
                "SELECT title FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("A conversa selecionada não existe.")
            connection.executemany(
                """
                INSERT INTO messages
                    (conversation_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (conversation_id, "user", user_content, "{}", now),
                    (
                        conversation_id,
                        "assistant",
                        str(result["answer"]),
                        json.dumps(assistant_metadata, ensure_ascii=False),
                        now,
                    ),
                ),
            )
            if exists["title"] == "Nova conversa":
                previous_messages = connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()[0]
                if previous_messages == 2:
                    connection.execute(
                        "UPDATE conversations SET title = ? WHERE id = ?",
                        (title, conversation_id),
                    )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def list_messages(
        self, conversation_id: int | None = None
    ) -> list[dict[str, object]]:
        with self._connect() as connection:
            if conversation_id is None:
                row = connection.execute(
                    "SELECT id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    return []
                conversation_id = row["id"]
            rows = connection.execute(
                """
                SELECT role, content, metadata, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
        messages: list[dict[str, object]] = []
        for role, content, metadata, created_at in rows:
            message: dict[str, object] = {
                "role": role,
                "content": content,
                "created_at": created_at,
            }
            message.update(json.loads(metadata))
            messages.append(message)
        return messages

    def list_gallery_items(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.conversation_id, c.title, m.role, m.content,
                       m.metadata, m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                ORDER BY m.id
                """
            ).fetchall()
        prompts: dict[int, str] = {}
        items: list[dict[str, object]] = []
        for row in rows:
            conversation_id = row["conversation_id"]
            if row["role"] == "user":
                prompts[conversation_id] = row["content"]
                continue
            metadata = json.loads(row["metadata"])
            if not isinstance(metadata, dict):
                continue
            for kind, key in (("images", "image_url"), ("videos", "video_url")):
                url = metadata.get(key)
                match = GALLERY_MEDIA_URL.fullmatch(url) if isinstance(url, str) else None
                if match is None or match["kind"] != kind:
                    continue
                filename = match["filename"]
                if kind == "videos" and not filename.endswith(".webm"):
                    continue
                items.append(
                    {
                        "type": "image" if kind == "images" else "video",
                        "url": url,
                        "prompt": prompts.get(conversation_id, ""),
                        "created_at": row["created_at"],
                        "conversation_id": conversation_id,
                        "conversation_title": row["title"],
                    }
                )
        return list(reversed(items))

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM messages")
            connection.execute("DELETE FROM conversations")
            self._insert_conversation(connection, "Nova conversa")

    @staticmethod
    def _insert_conversation(connection: sqlite3.Connection, title: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO conversations (title, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (title, now, now),
        )
        return int(cursor.lastrowid)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
