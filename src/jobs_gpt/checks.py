"""Read-only preflight and local ledger consistency diagnostics."""

import json
import os
import sqlite3
import sys
from collections import Counter
from importlib.metadata import version

from .models import read_profile
from .privacy import private_path
from .state import STOP


def doctor(workspace, profile=None, check_browser=False):
    root = private_path(workspace)
    errors = []
    result = {
        "python": sys.version.split()[0],
        "dependencies": {
            name: version(name) for name in ("jobs-gpt", "playwright", "pypdf")
        },
        "workspace_exists": root.exists(),
        "gemini_key_present": bool(
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ),
        "gemini_model_configured": bool(os.environ.get("GEMINI_MODEL")),
    }
    if root.exists() and not root.is_dir():
        errors.append("Workspace path is not a directory")
    elif root.exists() and os.name != "nt" and root.stat().st_mode & 0o077:
        errors.append(
            "Workspace permissions should be 0700; use a dedicated private directory"
        )
    path = root / "applications.sqlite3"
    if path.is_symlink():
        errors.append("Ledger must not be a symlink; inspect without following it")
    elif path.exists():
        con = None
        try:
            # No ledger writes, repairs, or implicit migrations during doctor.
            con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            integrity = con.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                errors.append("SQLite integrity check failed; reconcile manually")
            rows = con.execute(
                "SELECT job_key,status,payload FROM applications"
            ).fetchall()
            for _, status, payload in rows:
                try:
                    record = json.loads(payload)
                except (ValueError, TypeError):
                    errors.append("Malformed ledger record JSON; reconcile manually")
                    continue
                if not isinstance(record, dict):
                    errors.append("Ledger record is not an object; reconcile manually")
                    continue
                if record.get("status") != status:
                    errors.append("Ledger status/payload mismatch")
                evidence = record.get("outcome_evidence")
                if not isinstance(evidence, dict):
                    evidence = {}
                if status == "submitted" and not (
                    record.get("confirmation")
                    or record.get("confirmation_excerpt")
                    or evidence.get("receipt")
                ):
                    errors.append(
                        "A submitted record lacks receipt evidence; reconcile manually"
                    )
            counts = dict(Counter(row[1] for row in rows))
            result["ledger"] = {
                "integrity": integrity,
                "records": len(rows),
                "counts": counts,
                "protected_unresolved": sum(
                    counts.get(status, 0) for status in STOP - {"submitted"}
                ),
            }
        except (sqlite3.DatabaseError, OSError):
            # Avoid a raw SQLite traceback or accidentally printing applicant
            # payloads when a ledger is corrupt or has an unknown schema.
            errors.append("Ledger is unreadable or invalid; reconcile manually")
            result["ledger"] = {
                "integrity": "error",
                "records": None,
                "counts": {},
                "protected_unresolved": None,
            }
        finally:
            if con is not None:
                con.close()
    if profile:
        read_profile(profile)
        result["profile_and_pdf_valid"] = True
    if check_browser:
        from .browser import version as browser_version

        metadata = json.loads((root / "browser.json").read_text())
        actual = browser_version(metadata["endpoint"])
        matched = metadata.get("websocket") == actual["webSocketDebuggerUrl"]
        result["browser_identity_matches"] = matched
        if not matched:
            errors.append("Browser endpoint identity differs from workspace metadata")
    result["errors"] = errors
    result["ok"] = not errors
    return result
