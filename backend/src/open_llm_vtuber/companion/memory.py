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
        from .memory_migrations import migrate_provenance
        migrate_provenance(self.db)
        self.db.execute("CREATE TABLE IF NOT EXISTS assistant_memory_dependencies(source_id INTEGER NOT NULL,memory_id INTEGER NOT NULL,PRIMARY KEY(source_id,memory_id))")
        self.db.commit()

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
        self, kind: str, text: str, scene: str = "daily", speech: str | None = None, *, dependencies=None, memory_ids=None
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
            source_id = cur.lastrowid
            if kind == "assistant":
                parents = dependencies if dependencies is not None else [r[0] for r in self.db.execute(
                    "SELECT id FROM sources WHERE epoch=? AND conversation_turn=? AND id<? AND redacted=0 AND prompt_excluded=0",
                    (self.epoch, self.current_turn, source_id))]
                self.db.executemany("INSERT OR IGNORE INTO source_dependencies VALUES(?,?)",
                                    [(source_id, parent) for parent in parents if parent != source_id])
                self.db.executemany("INSERT OR IGNORE INTO assistant_memory_dependencies VALUES(?,?)",
                                    [(source_id, memory) for memory in (memory_ids or [])])
        return source_id

    def sources(self, scene: str | None = None, limit: int = 40) -> list[dict]:
        query = "SELECT * FROM sources WHERE redacted=0 AND prompt_excluded=0 AND epoch=?"
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
                "SELECT id FROM sources WHERE redacted=0 AND prompt_excluded=0 AND epoch=? AND scene=? AND kind!='assistant' AND id<? ORDER BY id DESC LIMIT 1",
                (self.epoch, scene, rows[0]["id"]),
            ).fetchone()
            if boundary:
                rows = [
                    dict(r)
                    for r in self.db.execute(
                        "SELECT * FROM sources WHERE redacted=0 AND prompt_excluded=0 AND epoch=? AND scene=? AND id>=? ORDER BY id",
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
        if not isinstance(ids, list) or not ids or len(ids) > 20000 or any(type(i) is not int or i < 1 for i in ids):
            return []
        rows = self.db.execute(
            f"SELECT * FROM sources WHERE redacted=0 {'AND prompt_excluded=0' if hasattr(self, 'epoch') else ''} AND id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        return [dict(r) for r in rows] if len(rows) == len(set(ids)) and (not hasattr(self, "epoch") or all(r["epoch"] == self.epoch for r in rows)) else []

    @property
    def revision(self):
        return int(self.db.execute("SELECT value FROM profile WHERE key='_memory_revision'").fetchone()[0])

    def _changed(self):
        self.db.execute("UPDATE profile SET value=CAST(value AS INTEGER)+1 WHERE key='_memory_revision'")

    def remember(self, kind, text, source_ids, evidence):
        from .memory_types import MemoryCandidate
        return self.record_memory(MemoryCandidate(kind, text, source_ids, evidence))

    def record_memory(self, candidate):
        rows = self._valid_sources(candidate.source_ids)
        if (candidate.kind not in {"fact", "preference", "experience", "commitment"}
            or not isinstance(candidate.text, str) or not candidate.text.strip() or len(candidate.text) > 600
            or not rows or not isinstance(candidate.evidence, str) or not candidate.evidence.strip()
            or any(r["kind"] == "assistant" for r in rows)
            or not any(candidate.evidence in r["text"] for r in rows)):
            return None
        origin = "user" if all(r["kind"] == "user" for r in rows) else "observation"
        scenes = {r["scene"] for r in rows}
        if origin == "observation" and len(scenes) != 1:
            return None
        scene = rows[0]["scene"] if origin == "observation" else None
        duplicate = self.db.execute("SELECT id FROM memories WHERE text=? AND scene IS ? AND epoch=? AND state='active'",
                                    (candidate.text, scene, self.epoch)).fetchone()
        if duplicate:
            return duplicate["id"]
        previous = None
        if candidate.previous_id is not None:
            previous = self.db.execute("SELECT * FROM memories WHERE id=? AND epoch=? AND state='active'",
                                       (candidate.previous_id, self.epoch)).fetchone()
            if not previous or previous["scene"] != scene or previous["kind"] != candidate.kind:
                return None
        state = "pending" if previous and not candidate.explicit_change else "active"
        with self.db:
            if previous and state == "active":
                self.db.execute("UPDATE memories SET state='superseded' WHERE id=?", (previous["id"],))
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,source_ids,evidence,origin,scene,epoch,state,topic,previous_id,event_time,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate.kind, candidate.text.strip(), json.dumps(candidate.source_ids), candidate.evidence, origin, scene,
                 self.epoch, state, previous["topic"] if previous else None,
                 candidate.previous_id, candidate.event_time, datetime.now(timezone.utc).isoformat()))
            item_id = cur.lastrowid
            if not previous:
                self.db.execute("UPDATE memories SET topic=? WHERE id=?", (item_id, item_id))
            self._changed()
        return item_id

    def memories(self, *, archive=False, include_history=False):
        if not hasattr(self, "epoch"):
            return [dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id DESC")]
        query = "SELECT * FROM memories WHERE epoch=?"
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(memories)')}
        if not archive and not include_history and 'state' in columns:
            query += " AND state='active'"
        return [dict(r) for r in self.db.execute(query + " ORDER BY id DESC", (0 if archive else self.epoch,))]

    def _invalidate(self, item_id, erase=False):
        row = self.db.execute("SELECT * FROM memories WHERE id=? AND epoch=?", (item_id, self.epoch)).fetchone()
        if not row:
            raise KeyError(item_id)
        memories = self.memories(include_history=True)
        affected = {m['id'] for m in memories if m['topic'] == row['topic']}
        ids = {source for m in memories if m['id'] in affected for source in json.loads(m['source_ids'])}
        ids.update(r['source_id'] for r in self.db.execute("SELECT * FROM assistant_memory_dependencies") if r['memory_id'] in affected)
        while True:
            expanded = set(ids)
            for dep in self.db.execute("SELECT source_id,parent_id FROM source_dependencies"):
                if dep['parent_id'] in ids:
                    expanded.add(dep['source_id'])
            for memory in memories:
                if expanded.intersection(json.loads(memory['source_ids'])):
                    affected.add(memory['id'])
                    expanded.update(json.loads(memory['source_ids']))
            if expanded == ids:
                break
            ids = expanded
        for source in ids:
            self.db.execute("UPDATE sources SET prompt_excluded=1 WHERE id=? AND epoch=?", (source, self.epoch))
            if erase:
                self.db.execute("UPDATE sources SET redacted=1,text='',speech=NULL WHERE id=? AND epoch=?", (source, self.epoch))
        for memory in affected:
            self.db.execute("UPDATE memories SET state='excluded' WHERE id=?", (memory,))
            if erase:
                self.db.execute("UPDATE memories SET text='',evidence='' WHERE id=?", (memory,))
        for summary in self.db.execute("SELECT scene,source_ids FROM summaries WHERE epoch=?", (self.epoch,)).fetchall():
            if ids.intersection(json.loads(summary['source_ids'])):
                self.db.execute("DELETE FROM summaries WHERE scene=? AND epoch=?", (summary['scene'], self.epoch))
        self._changed()
        return dict(row)

    def forget(self, item_id, mode="exclude"):
        if mode not in {"exclude", "erase"}:
            raise ValueError("遗忘模式无效")
        with self.db:
            self._invalidate(item_id, erase=mode == 'erase')

    def correct(self, item_id, text):
        if not text.strip() or len(text) > 600:
            raise ValueError("记忆需为 1–600 字")
        with self.db:
            old = self._invalidate(item_id)
            self.db.execute("UPDATE memories SET state='superseded' WHERE id=?", (item_id,))
            cur = self.db.execute(
                "INSERT INTO memories(kind,text,source_ids,evidence,origin,scene,epoch,state,topic,previous_id,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (old['kind'], text.strip(), '[]', '用户直接修改', 'user_edit', old['scene'], self.epoch, 'active',
                 old['topic'], item_id, datetime.now(timezone.utc).isoformat()))
        return cur.lastrowid

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
