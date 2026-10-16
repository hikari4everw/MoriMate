import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:
    """Source-backed memories. Redaction also invalidates derived summaries."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
                scene TEXT NOT NULL, created TEXT NOT NULL,
                redacted INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
                source_ids TEXT NOT NULL, evidence TEXT NOT NULL, origin TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                scene TEXT PRIMARY KEY, text TEXT NOT NULL, source_ids TEXT NOT NULL
            );
        """)

        columns = {row[1] for row in self.db.execute("PRAGMA table_info(sources)")}
        if "speech" not in columns:
            self.db.execute("ALTER TABLE sources ADD COLUMN speech TEXT")
            self.db.commit()

        columns = {row[1] for row in self.db.execute("PRAGMA table_info(memories)")}
        if "scene" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE memories ADD COLUMN scene TEXT")
                for memory in self.memories():
                    if memory["origin"] in {"observation", "user_edit"}:
                        rows = self._valid_sources(json.loads(memory["source_ids"]))
                        self.db.execute(
                            "UPDATE memories SET scene=? WHERE id=?",
                            (rows[0]["scene"] if rows else "unknown", memory["id"]),
                        )

    def close(self):
        self.db.close()

    def add_source(
        self, kind: str, text: str, scene: str = "daily", speech: str | None = None
    ) -> int:
        if kind not in {"user", "assistant", "observation"}:
            raise ValueError("Unknown source kind")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO sources(kind,text,scene,created,speech) VALUES(?,?,?,?,?)",
                (kind, text, scene, datetime.now(timezone.utc).isoformat(), speech),
            )
        return cur.lastrowid

    def sources(self, scene: str | None = None, limit: int = 40) -> list[dict]:
        query = "SELECT * FROM sources WHERE redacted=0"
        args = []
        if scene is not None:
            query += " AND scene=?"
            args.append(scene)
        rows = self.db.execute(
            query + " ORDER BY id DESC LIMIT ?", (*args, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def _valid_sources(self, ids: list[int]) -> list[dict]:
        if not ids or len(ids) > 20000:
            return []
        rows = self.db.execute(
            f"SELECT * FROM sources WHERE redacted=0 AND id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows] if len(rows) == len(set(ids)) else []

    def remember(
        self, kind: str, text: str, source_ids: list[int], evidence: str
    ) -> int | None:
        rows = self._valid_sources(source_ids)
        if (
            kind not in {"fact", "preference", "experience"}
            or not text.strip()
            or len(text) > 600
        ):
            return None
        if (
            not rows
            or not evidence.strip()
            or any(r["kind"] == "assistant" for r in rows)
        ):
            return None
        if not any(evidence in r["text"] for r in rows):
            return None
        origin = "user" if all(r["kind"] == "user" for r in rows) else "observation"
        scenes = {r["scene"] for r in rows}
        if origin == "observation" and len(scenes) != 1:
            return None
        scene = rows[0]["scene"] if origin == "observation" else None
        duplicate = self.db.execute(
            "SELECT id FROM memories WHERE text=? AND scene IS ?", (text, scene)
        ).fetchone()
        if duplicate:
            return duplicate["id"]
        with self.db:
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,source_ids,evidence,origin,scene) VALUES(?,?,?,?,?,?)",
                (kind, text, json.dumps(source_ids), evidence, origin, scene),
            )
        return cur.lastrowid

    def memories(self) -> list[dict]:
        return [
            dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id DESC")
        ]

    def _redact_dependencies(self, item_id: int):
        row = self.db.execute(
            "SELECT source_ids FROM memories WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            raise KeyError(item_id)
        ids = set(json.loads(row["source_ids"]))
        ids.update(
            r["id"]
            for r in self.db.execute(
                "SELECT id FROM sources WHERE kind='assistant' AND redacted=0"
            )
        )
        for source in ids:
            self.db.execute(
                "UPDATE sources SET redacted=1,text='',speech=NULL WHERE id=?",
                (source,),
            )
        for summary in self.db.execute(
            "SELECT scene,source_ids FROM summaries"
        ).fetchall():
            if ids.intersection(json.loads(summary["source_ids"])):
                self.db.execute(
                    "DELETE FROM summaries WHERE scene=?", (summary["scene"],)
                )
        for memory in self.db.execute("SELECT id,source_ids FROM memories").fetchall():
            if ids.intersection(json.loads(memory["source_ids"])):
                self.db.execute("DELETE FROM memories WHERE id=?", (memory["id"],))
        # Assistant paraphrases can otherwise repeat the forgotten fact next turn.
        self.db.execute(
            "UPDATE sources SET redacted=1,text='',speech=NULL WHERE kind='assistant'"
        )

    def forget(self, item_id: int):
        with self.db:
            self._redact_dependencies(item_id)
            self.db.execute("DELETE FROM memories WHERE id=?", (item_id,))

    def correct(self, item_id: int, text: str):
        if not text.strip() or len(text) > 600:
            raise ValueError("记忆需为 1–600 字")
        with self.db:
            row = self.db.execute(
                "SELECT kind,scene FROM memories WHERE id=?", (item_id,)
            ).fetchone()
            if not row:
                raise KeyError(item_id)
            self._redact_dependencies(item_id)
            self.db.execute(
                "INSERT OR REPLACE INTO memories(id,kind,text,source_ids,evidence,origin,scene) VALUES(?,?,?,?,?,?,?)",
                (
                    item_id,
                    row["kind"],
                    text.strip(),
                    "[]",
                    "用户直接修改",
                    "user_edit",
                    row["scene"],
                ),
            )

    def save_summary(self, scene: str, text: str, source_ids: list[int]) -> str | None:
        if not text.strip() or len(text) > 3000 or not self._valid_sources(source_ids):
            return None
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO summaries VALUES(?,?,?)",
                (scene, text, json.dumps(sorted(set(source_ids)))),
            )
        return text

    def summary(self, scene: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM summaries WHERE scene=?", (scene,)
        ).fetchone()
        return dict(row) if row else None

    def mark_processed(self, ids: list[int]):
        with self.db:
            self.db.executemany(
                "UPDATE sources SET processed=1 WHERE id=?", [(i,) for i in ids]
            )

    def clear_scene(self, scene: str):
        with self.db:
            self.db.execute("DELETE FROM memories WHERE scene=?", (scene,))
            ids = {
                r["id"]
                for r in self.db.execute(
                    "SELECT id FROM sources WHERE scene=?", (scene,)
                )
            }
            for row in self.db.execute("SELECT id,source_ids FROM memories").fetchall():
                if ids.intersection(json.loads(row["source_ids"])):
                    self.db.execute("DELETE FROM memories WHERE id=?", (row["id"],))
            self.db.execute(
                "UPDATE sources SET redacted=1,text='',speech=NULL WHERE scene=?",
                (scene,),
            )
            self.db.execute("DELETE FROM summaries WHERE scene=?", (scene,))
