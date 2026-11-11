#!/usr/bin/env python3
"""Install repo-owned skill files; retain private runtime configuration separately."""

import argparse
import json
import shutil
from pathlib import Path

from jobs_gpt.privacy import write_private


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination", type=Path, default=Path.home() / ".codex/skills"
    )
    parser.add_argument(
        "--workspace", type=Path, help="Explicitly replace the private workspace hint"
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    for name in ("job-applications", "jobs-gpt-maintenance"):
        destination = args.destination / name
        shutil.copytree(repo / "skills" / name, destination, dirs_exist_ok=True)
        # Preserve an existing custom workspace unless the user explicitly changes it.
        runtime = destination / "runtime.json"
        config = json.loads(runtime.read_text()) if runtime.exists() else {}
        workspace = args.workspace or Path(
            config.get("workspace", Path.home() / ".local/share/jobs-gpt")
        )
        config.update(repo=str(repo), workspace=str(workspace.expanduser().resolve()))
        write_private(runtime, config)
        print("Installed " + name)


if __name__ == "__main__":
    main()
