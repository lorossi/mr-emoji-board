"""SQLite store of the channel's MR posts, so a harvest only scans recent posts and re-reads known ones."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS mr_posts (
    id        TEXT PRIMARY KEY,
    create_at INTEGER NOT NULL,          -- ms, like Mattermost
    root      TEXT    NOT NULL,          -- the post, trimmed to what MergeRequest.from_post reads
    thread    TEXT    NOT NULL,          -- replies, trimmed to what MergeRequest.attach_thread reads
    done      INTEGER NOT NULL DEFAULT 0 -- merged/closed by emoji and GitLab: no longer re-read
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _trim_root(post: dict) -> dict:
    return {
        "id": post["id"],
        "create_at": post["create_at"],
        "user_id": post["user_id"],
        "message": post["message"],
        "metadata": {"reactions": post.get("metadata", {}).get("reactions", [])},
    }


def _trim_reply(post: dict) -> dict:
    return {"create_at": post["create_at"], "user_id": post["user_id"]}


class PostStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    def last_scan(self) -> int | None:
        """When the channel was last scanned (ms), or None before the first scan."""
        row = self._db.execute("SELECT value FROM meta WHERE key = 'last_scan'").fetchone()
        return int(row[0]) if row else None

    def set_last_scan(self, at: int) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_scan', ?)", (str(at),)
            )

    def save(self, root: dict, replies: list[dict]) -> None:
        """Insert or update an MR post with its replies (keeps the done flag)."""
        with self._db:
            self._db.execute(
                """INSERT INTO mr_posts (id, create_at, root, thread) VALUES (?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET root = excluded.root, thread = excluded.thread""",
                (
                    root["id"],
                    root["create_at"],
                    json.dumps(_trim_root(root)),
                    json.dumps([_trim_reply(r) for r in replies]),
                ),
            )

    def delete(self, post_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM mr_posts WHERE id = ?", (post_id,))

    def mark_done(self, post_ids: list[str]) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE mr_posts SET done = 1 WHERE id = ?", [(i,) for i in post_ids]
            )

    def active_ids(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT id FROM mr_posts WHERE done = 0")]

    def load(self, since: int) -> list[tuple[dict, list[dict]]]:
        """(post, replies) of every active MR, and of finished ones posted since `since` (ms)."""
        rows = self._db.execute(
            "SELECT root, thread FROM mr_posts WHERE done = 0 OR create_at >= ?", (since,)
        )
        return [(json.loads(root), json.loads(thread)) for root, thread in rows]
