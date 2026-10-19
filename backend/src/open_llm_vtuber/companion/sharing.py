"""Persistent anonymous guest identities and password verification."""

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class GuestRegistry:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "sharing.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS guests (
                number INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, identity_hash TEXT UNIQUE NOT NULL,
                created TEXT NOT NULL, last_seen TEXT NOT NULL
            );
        """)

    @property
    def has_password(self):
        return (
            self.db.execute("SELECT 1 FROM settings WHERE key='password'").fetchone()
            is not None
        )

    def set_password(self, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError("访问密码需为 8–128 字符")
        salt = secrets.token_hex(16)
        hashed = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('password', ?)",
                (salt + ":" + hashed,),
            )

    def check_password(self, password):
        if not isinstance(password, str) or len(password) > 128:
            return False
        row = self.db.execute(
            "SELECT value FROM settings WHERE key='password'"
        ).fetchone()
        if not row:
            return False
        salt, expected = row[0].split(":")
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        return hmac.compare_digest(expected, actual)

    def identify(self, token):
        row = (
            self.db.execute(
                "SELECT * FROM guests WHERE identity_hash=?", (digest(token),)
            ).fetchone()
            if token
            else None
        )
        if row is None:
            token = secrets.token_urlsafe(32)
            guest_id, created = uuid.uuid4().hex, now()
            with self.db:
                self.db.execute(
                    "INSERT INTO guests(id,identity_hash,created,last_seen) VALUES (?,?,?,?)",
                    (guest_id, digest(token), created, created),
                )
            row = self.db.execute(
                "SELECT * FROM guests WHERE id=?", (guest_id,)
            ).fetchone()
        self.touch(row["id"])
        return self.public(row), token

    @staticmethod
    def public(row):
        return {
            "id": row["id"],
            "label": f"访客{row['number']:03d}",
            "created": row["created"],
            "last_seen": row["last_seen"],
        }

    def touch(self, guest_id):
        with self.db:
            self.db.execute(
                "UPDATE guests SET last_seen=? WHERE id=?", (now(), guest_id)
            )

    def get(self, guest_id):
        row = self.db.execute("SELECT * FROM guests WHERE id=?", (guest_id,)).fetchone()
        if row is None:
            raise ValueError("访客不存在")
        return self.public(row)

    def list(self):
        return [
            self.public(row)
            for row in self.db.execute("SELECT * FROM guests ORDER BY number DESC")
        ]

    def database(self, guest_id):
        # Validate against the registry; browser-provided paths never reach the filesystem.
        self.get(guest_id)
        return self.root / "guests" / f"{guest_id}.sqlite3"

    def delete(self, guest_id):
        path = self.database(guest_id)
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        with self.db:
            self.db.execute("DELETE FROM guests WHERE id=?", (guest_id,))

    def close(self):
        self.db.close()
