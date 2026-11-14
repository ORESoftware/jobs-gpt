"""Private artifacts never belong inside a Git checkout; writes are atomic."""

import json
import os
import tempfile
from pathlib import Path


def private_path(path):
    path = Path(path).expanduser().resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise ValueError(
                "Private applicant/state artifacts must be outside Git checkouts"
            )
    return path


def private_directory(path):
    path = private_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir(mode=0700) does not harden an already existing directory.
    # Refuse to expose applicant records in a pre-existing shared directory.
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError(
            "Private artifact directory must have permissions 0700; "
            "fix existing permissions explicitly"
        )
    return path


def write_private(path, value):
    path = private_path(path)
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + "-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
