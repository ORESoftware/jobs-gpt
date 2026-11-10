"""Transactional local ledger. Submission intent survives process crashes."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import now

STOP = {
    "submitted",
    "submitting",
    "unconfirmed",
    "captcha_blocked",
    "application_limit",
}
ALLOWED_ATTEMPTS = {
    "preview",
    "needs_review",
    "drafted",
    "validation_rejected",
    "ready",
}


class Store:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "applications.sqlite3"
        with self.connect() as con:
            con.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS applications(job_key TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_key TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);""")

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=30)

    def get(self, key):
        with self.connect() as con:
            row = con.execute(
                "SELECT payload FROM applications WHERE job_key=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.connect() as con:
            rows = con.execute(
                "SELECT job_key,payload FROM applications ORDER BY job_key"
            ).fetchall()
        return {key: json.loads(payload) for key, payload in rows}

    def put(self, key, payload, event="updated"):
        payload = dict(payload, updated_at=now())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM applications WHERE job_key=?", (key,)
            ).fetchone()
            if row and row[0] in STOP and payload["status"] != row[0]:
                raise ValueError(
                    "Protected submission state requires outcome evidence, not an automatic retry"
                )
            con.execute(
                "INSERT INTO applications VALUES(?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at",
                (key, payload["status"], json.dumps(payload), payload["updated_at"]),
            )
            con.execute(
                "INSERT INTO events(job_key,event,payload,created_at) VALUES(?,?,?,?)",
                (key, event, json.dumps(payload), now()),
            )
        return payload

    def begin_attempt(self, key, payload):
        """Atomic compare-and-set: two workers cannot both obtain permission to click."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM applications WHERE job_key=?", (key,)
            ).fetchone()
            if not row or row[0] not in ALLOWED_ATTEMPTS:
                return False
            value = dict(
                payload, status="submitting", submit_attempt_at=now(), updated_at=now()
            )
            con.execute(
                "UPDATE applications SET status=?,payload=?,updated_at=? WHERE job_key=?",
                ("submitting", json.dumps(value), value["updated_at"], key),
            )
            con.execute(
                "INSERT INTO events(job_key,event,payload,created_at) VALUES(?,?,?,?)",
                (key, "submit_intent", json.dumps(value), now()),
            )
        return True

    def outcome(self, key, payload, evidence):
        if not evidence:
            raise ValueError("An outcome needs concrete evidence")
        if payload["status"] not in (
            "submitted",
            "unconfirmed",
            "captcha_blocked",
            "application_limit",
            "validation_rejected",
        ):
            raise ValueError("Invalid submission outcome")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM applications WHERE job_key=?", (key,)
            ).fetchone()
            if not row or row[0] not in STOP:
                raise ValueError(
                    "No recorded submission attempt or protected outcome to resolve"
                )
            if row[0] == "submitted" and payload["status"] != "submitted":
                raise ValueError("Confirmed submissions cannot be downgraded")
            value = dict(payload, outcome_evidence=evidence, updated_at=now())
            con.execute(
                "UPDATE applications SET status=?,payload=?,updated_at=? WHERE job_key=?",
                (value["status"], json.dumps(value), value["updated_at"], key),
            )
            con.execute(
                "INSERT INTO events(job_key,event,payload,created_at) VALUES(?,?,?,?)",
                (key, "outcome", json.dumps(value), now()),
            )
        return value

    def import_record(self, key, payload):
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO applications VALUES(?,?,?,?)",
                (key, payload["status"], json.dumps(payload), now()),
            )


@contextmanager
def workspace_lock(workspace):
    path = Path(workspace) / ".runner.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if __import__("os").name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise RuntimeError("Another controller is using this workspace") from None
        try:
            yield
        finally:
            if __import__("os").name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
