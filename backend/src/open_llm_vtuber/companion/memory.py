import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:
    """Source-backed memories. Redaction also invalidates derived summaries."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        self.current_turn = None
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
            CREATE TABLE IF NOT EXISTS profile (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
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

        self.migrate_v2()

    def migrate_v2(self):
        from .memory_migrations import migrate_v2
        result = migrate_v2(self.db, self.path)
        self.epoch = result["epoch"]
        return result

    def archive(self, before=None, limit=60):
        return {
            "sources": self.history_page(before, limit, archive=True),
            "memories": self.memories(archive=True),
            "summaries": [dict(row) for row in self.db.execute("SELECT * FROM summaries WHERE epoch=0")],
        }

    def close(self):
        self.db.close()

    def persona(self, default: str) -> str:
        row = self.db.execute(
            "SELECT value FROM profile WHERE key='persona'"
        ).fetchone()
        return row[0] if row else default

    def save_persona(self, text: str | None):
        if text is not None and (
            not isinstance(text, str) or not text.strip() or len(text) > 16000
        ):
            raise ValueError("人格提示词需为 1–16000 字，且不能全为空白")
        with self.db:
            if text is None:
                self.db.execute("DELETE FROM profile WHERE key='persona'")
            else:
                self.db.execute(
                    "INSERT OR REPLACE INTO profile VALUES ('persona', ?)", (text,)
                )

    def history_page(self, before: int | None = None, limit: int = 60, *, archive=False) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM sources WHERE redacted=0 AND epoch=? AND (? IS NULL OR id<?) "
            "ORDER BY id DESC LIMIT ?",
            (0 if archive else self.epoch, before, before, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_source(
        self, kind: str, text: str, scene: str = "daily", speech: str | None = None
    ) -> int:
        if kind not in {"user", "assistant", "observation"}:
            raise ValueError("Unknown source kind")
        with self.db:
            if kind == "user" or (kind == "assistant" and self.current_turn is None):
                turn = self.db.execute("INSERT INTO conversation_turns(scene,epoch) VALUES(?,?)", (scene, self.epoch))
                self.current_turn = turn.lastrowid
            cur = self.db.execute(
                "INSERT INTO sources(kind,text,scene,created,speech,epoch,conversation_turn) VALUES(?,?,?,?,?,?,?)",
                (kind, text, scene, datetime.now(timezone.utc).isoformat(), speech, self.epoch, self.current_turn if kind != "observation" else None),
            )
        return cur.lastrowid

    def sources(self, scene: str | None = None, limit: int = 40) -> list[dict]:
        query = "SELECT * FROM sources WHERE redacted=0 AND epoch=?"
        args = [self.epoch]
        if scene is not None:
            query += " AND scene=?"
            args.append(scene)
        rows = self.db.execute(
            query + " ORDER BY id DESC LIMIT ?", (*args, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def conversation_sources(self, scene: str, limit: int = 20) -> list[dict]:
        rows = self.sources(scene, limit)
        if rows and rows[0]["kind"] == "assistant":
            boundary = self.db.execute(
                "SELECT id FROM sources WHERE redacted=0 AND epoch=? AND scene=? AND kind!='assistant' AND id<? ORDER BY id DESC LIMIT 1",
                (self.epoch, scene, rows[0]["id"]),
            ).fetchone()
            if boundary:
                rows = [
                    dict(r)
                    for r in self.db.execute(
                        "SELECT * FROM sources WHERE redacted=0 AND epoch=? AND scene=? AND id>=? ORDER BY id",
                        (self.epoch, scene, boundary["id"]),
                    )
                ]
        return rows

    def recorded_range(self, source_ids: list[int]) -> dict | None:
        rows = self._valid_sources(source_ids)
        if not rows:
            return None
        dates = sorted(r["created"] for r in rows)
        return {"recorded_from": dates[0], "recorded_to": dates[-1]}

    def _valid_sources(self, ids: list[int]) -> list[dict]:
        if not ids or len(ids) > 20000:
            return []
        rows = self.db.execute(
            f"SELECT * FROM sources WHERE redacted=0 AND id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows] if len(rows) == len(set(ids)) and (not hasattr(self, "epoch") or all(r["epoch"] == self.epoch for r in rows)) else []

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
            "SELECT id FROM memories WHERE text=? AND scene IS ? AND epoch=?", (text, scene, self.epoch)
        ).fetchone()
        if duplicate:
            return duplicate["id"]
        with self.db:
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,source_ids,evidence,origin,scene,epoch) VALUES(?,?,?,?,?,?,?)",
                (kind, text, json.dumps(source_ids), evidence, origin, scene, self.epoch),
            )
        return cur.lastrowid

    def memories(self, *, archive=False) -> list[dict]:
        if not hasattr(self, "epoch"):
            return [dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id DESC")]
        return [dict(r) for r in self.db.execute("SELECT * FROM memories WHERE epoch=? ORDER BY id DESC", (0 if archive else self.epoch,))]

    def _redact_dependencies(self, item_id: int):
        row = self.db.execute(
            "SELECT source_ids FROM memories WHERE id=? AND epoch=?", (item_id, self.epoch)
        ).fetchone()
        if not row:
            raise KeyError(item_id)
        ids = set(json.loads(row["source_ids"]))
        ids.update(
            r["id"]
            for r in self.db.execute(
                "SELECT id FROM sources WHERE kind='assistant' AND redacted=0 AND epoch=?", (self.epoch,)
            )
        )
        for source in ids:
            self.db.execute(
                "UPDATE sources SET redacted=1,text='',speech=NULL WHERE id=?",
                (source,),
            )
        for summary in self.db.execute(
            "SELECT scene,source_ids FROM summaries WHERE epoch=?", (self.epoch,)
        ).fetchall():
            if ids.intersection(json.loads(summary["source_ids"])):
                self.db.execute(
                    "DELETE FROM summaries WHERE scene=? AND epoch=?", (summary["scene"], self.epoch)
                )
        for memory in self.db.execute("SELECT id,source_ids FROM memories WHERE epoch=?", (self.epoch,)).fetchall():
            if ids.intersection(json.loads(memory["source_ids"])):
                self.db.execute("DELETE FROM memories WHERE id=?", (memory["id"],))
        # Assistant paraphrases can otherwise repeat the forgotten fact next turn.
        self.db.execute(
            "UPDATE sources SET redacted=1,text='',speech=NULL WHERE kind='assistant' AND epoch=?", (self.epoch,)
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
                "SELECT kind,scene FROM memories WHERE id=? AND epoch=?", (item_id, self.epoch)
            ).fetchone()
            if not row:
                raise KeyError(item_id)
            self._redact_dependencies(item_id)
            self.db.execute(
                "INSERT OR REPLACE INTO memories(id,kind,text,source_ids,evidence,origin,scene,epoch) VALUES(?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    row["kind"],
                    text.strip(),
                    "[]",
                    "用户直接修改",
                    "user_edit",
                    row["scene"],
                    self.epoch,
                ),
            )

    def save_summary(self, scene: str, text: str, source_ids: list[int]) -> str | None:
        if not text.strip() or len(text) > 3000 or not self._valid_sources(source_ids):
            return None
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO summaries(scene,text,source_ids,epoch) VALUES(?,?,?,?)",
                (scene, text, json.dumps(sorted(set(source_ids))), self.epoch),
            )
        return text

    def summary(self, scene: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM summaries WHERE scene=? AND epoch=?", (scene, self.epoch)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result.update(self.recorded_range(json.loads(row["source_ids"])) or {})
        return result

    def mark_processed(self, ids: list[int]):
        with self.db:
            self.db.executemany(
                "UPDATE sources SET processed=1 WHERE id=?", [(i,) for i in ids]
            )

    def clear_scene(self, scene: str):
        with self.db:
            self.db.execute("DELETE FROM memories WHERE scene=? AND epoch=?", (scene, self.epoch))
            ids = {
                r["id"]
                for r in self.db.execute(
                    "SELECT id FROM sources WHERE scene=? AND epoch=?", (scene, self.epoch)
                )
            }
            for row in self.db.execute("SELECT id,source_ids FROM memories WHERE epoch=?", (self.epoch,)).fetchall():
                if ids.intersection(json.loads(row["source_ids"])):
                    self.db.execute("DELETE FROM memories WHERE id=?", (row["id"],))
            self.db.execute(
                "UPDATE sources SET redacted=1,text='',speech=NULL WHERE scene=? AND epoch=?",
                (scene, self.epoch),
            )
            self.db.execute("DELETE FROM summaries WHERE scene=? AND epoch=?", (scene, self.epoch))
