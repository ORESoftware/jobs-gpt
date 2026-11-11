"""Transactional local ledger. Submission intent survives process crashes."""

import json
import os
import sqlite3
from contextlib import contextmanager

from .models import now
from .privacy import private_directory

STOP = {
    "submitted",
    "submitting",
    "unconfirmed",
    "captcha_blocked",
    "application_limit",
}
ALLOWED_ATTEMPTS = {"ready"}


class Store:
    def __init__(self, workspace):
        self.root = private_directory(workspace)
        if os.name != "nt" and self.root.stat().st_mode & 0o077:
            raise ValueError(
                "Workspace must have permissions 0700; use a dedicated private directory"
            )
        self.path = self.root / "applications.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Ledger must not be a symlink")
        with self.connect() as con:
            con.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS applications(job_key TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_key TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);""")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        try:
            with con:
                yield con
        finally:
            con.close()

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
                "SELECT status,payload FROM applications WHERE job_key=?", (key,)
            ).fetchone()
            if payload["status"] in ("submitted", "submitting", "unconfirmed") and (
                not row or row[0] != payload["status"]
            ):
                raise ValueError(
                    "Submission states require an intent or outcome, never put()"
                )
            if row and row[0] in STOP and payload["status"] != row[0]:
                raise ValueError(
                    "Protected submission state requires outcome evidence, not an automatic retry"
                )
            if row and row[0] in STOP:
                prior = json.loads(row[1])
                payload = dict(prior, **payload)
                for field in (
                    "submit_attempt_at",
                    "confirmation_before",
                    "outcome_evidence",
                    "confirmation",
                ):
                    if field in prior:
                        payload[field] = prior[field]
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
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("An outcome needs concrete evidence")
        if payload["status"] not in (
            "submitted",
            "unconfirmed",
            "captcha_blocked",
            "application_limit",
        ):
            raise ValueError("Invalid submission outcome")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status,payload FROM applications WHERE job_key=?", (key,)
            ).fetchone()
            if not row or row[0] not in STOP:
                raise ValueError(
                    "No recorded submission attempt or protected outcome to resolve"
                )
            if row[0] == "submitted" and payload["status"] != "submitted":
                raise ValueError("Confirmed submissions cannot be downgraded")
            if payload["status"] == "submitted" and not evidence.get("receipt"):
                raise ValueError("Confirmed outcome requires a receipt")
            prior = json.loads(row[1])
            value = dict(prior, **payload)
            for field in ("submit_attempt_at", "confirmation_before"):
                if field in prior:
                    value[field] = prior[field]
            value.update(outcome_evidence=evidence, updated_at=now())
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
    path = private_directory(workspace) / ".runner.lock"
    if path.is_symlink():
        raise ValueError("Workspace lock must not be a symlink")
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
