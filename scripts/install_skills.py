#!/usr/bin/env python3
"""Install repo-owned skill files; retain private runtime configuration separately."""

import argparse
import json
import os
import shutil
from pathlib import Path

from jobs_gpt.privacy import private_directory, write_private


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
        # Create the skill directory privately before copying files. The runtime
        # hint below must not land in a pre-existing world-readable directory.
        private_directory(destination)
        shutil.copytree(repo / "skills" / name, destination, dirs_exist_ok=True)
        # copytree copies source directory mode (often 0755) to the target.
        # Restore the restricted mode on our managed destination before runtime write.
        if os.name != "nt":
            destination.chmod(0o700)
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
