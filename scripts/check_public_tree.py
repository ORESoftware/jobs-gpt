#!/usr/bin/env python3
"""Check Git-visible source files for private artifacts and obvious credentials."""

import argparse
import re
import subprocess
from pathlib import Path

PRIVATE_NAMES = {
    "profile.json",
    "answers.json",
    "runtime.json",
    "browser.json",
    ".env",
    "Cookies",
    "Login Data",
    "History",
}
PRIVATE_DIRS = {"private", "workspace", "browser-profile", "screenshots"}
PRIVATE_SUFFIXES = {".pdf", ".docx", ".sqlite", ".sqlite3", ".db", ".har"}
SECRET = re.compile(
    r"AQ\.[A-Za-z0-9_-]{25,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
LOCAL_PATH = re.compile(r'/(?:Users|home)/[^\s/"\']+/')


def issues(path, content):
    file = Path(path)
    result = []
    if (
        file.name in PRIVATE_NAMES
        or file.suffix in PRIVATE_SUFFIXES
        or PRIVATE_DIRS.intersection(file.parts)
    ):
        result.append("private artifact")
    text = content.decode("utf-8", errors="replace")
    if SECRET.search(text):
        result.append("credential-like content")
    if LOCAL_PATH.search(text):
        result.append("machine-specific home path")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", action="store_true")
    args = parser.parse_args()
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )
    flags = (
        ["--cached"] if args.index else ["--cached", "--others", "--exclude-standard"]
    )
    files = (
        subprocess.check_output(["git", "ls-files", "-z"] + flags, cwd=root)
        .decode()
        .split("\0")
    )
    failed = []
    for path in sorted(set(filter(None, files))):
        if args.index:
            content = subprocess.check_output(["git", "show", ":" + path], cwd=root)
        elif (root / path).is_file():
            content = (root / path).read_bytes()
        else:
            continue
        for reason in issues(path, content):
            failed.append((path, reason))
    for path, reason in failed:
        print(path + ": " + reason)
    if failed:
        raise SystemExit(1)
    print(
        "Public source scan passed (" + str(len(set(filter(None, files)))) + " files)"
    )


if __name__ == "__main__":
    main()
