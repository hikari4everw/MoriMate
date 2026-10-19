import json
import sqlite3
from datetime import datetime, timezone


class UsageStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, created TEXT, data TEXT)')

    def record(self, record):
        with self.db:
            self.db.execute('INSERT INTO usage(created,data) VALUES (?,?)',
                            (datetime.now(timezone.utc).isoformat(), json.dumps(record)))

    def rows(self, limit=200):
        return [{"id": row[0], "created": row[1], **json.loads(row[2])}
                for row in self.db.execute('SELECT id,created,data FROM usage ORDER BY id DESC LIMIT ?', (limit,))]

    def close(self):
        self.db.close()
