#!/usr/bin/env python3
"""Resolve an installed package or local checkout without hardcoded machine paths."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    runtime = Path(__file__).resolve().parents[1] / "runtime.json"
    config = json.loads(runtime.read_text()) if runtime.exists() else {}
    repo = os.environ.get("JOBS_GPT_REPO") or config.get("repo")
    if repo:
        repo = Path(repo).expanduser().resolve()
        python = repo / (
            ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
        )
        if not (repo / "src/jobs_gpt/cli.py").is_file() or not python.is_file():
            raise SystemExit(
                "Configured checkout/virtualenv is missing; reinstall jobs-gpt or update JOBS_GPT_REPO"
            )
        command = [str(python), "-m", "jobs_gpt"]
    elif importlib.util.find_spec("jobs_gpt"):
        command = [sys.executable, "-m", "jobs_gpt"]
    else:
        raise SystemExit("Install jobs-gpt or set JOBS_GPT_REPO to its checkout")
    raise SystemExit(subprocess.call(command + sys.argv[1:]))


if __name__ == "__main__":
    main()
