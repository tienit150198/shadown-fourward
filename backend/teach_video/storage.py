"""Atomic job metadata; media is owned by an unguessable ticket, not a public URL."""
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, data TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def create(self):
        ident, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        data = {"job_id": ident, "status": "uploading", "created_at": time.time(), "updated_at": time.time()}
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, ?)", (ident, hashlib.sha256(token.encode()).hexdigest(), json.dumps(data)))
        (self.root / ident).mkdir()
        return data, token

    def get(self, ident, token=None):
        with self.connect() as db:
            row = db.execute("SELECT token_hash, data FROM jobs WHERE id=?", (ident,)).fetchone()
        if row is None or (token is not None and not secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())):
            raise KeyError(ident)
        return json.loads(row[1])

    def update(self, ident, **fields):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            data = {**json.loads(row[0]), **fields, "updated_at": time.time()}
            db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), ident))
        return data

    def all(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs")]

    def recover(self):
        # Never silently repeat a possibly billed AI call after a worker/process crash.
        for job in self.all():
            if job["status"] in ("uploading", "extracting", "transcribing", "learning", "verifying"):
                self.update(job["job_id"], status="interrupted", error="Máy chủ đã khởi động lại. Kiểm tra rồi bấm thử lại.")

    def directory(self, ident):
        self.get(ident)  # Only IDs in the database may address the filesystem.
        return self.root / ident
