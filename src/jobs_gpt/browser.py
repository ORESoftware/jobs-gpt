"""OS-owned Chromium: controller exit preserves unresolved tabs."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def local_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username
        or parsed.password
    ):
        raise ValueError("CDP endpoint must be an HTTP loopback address")
    return endpoint.rstrip("/")


def start(pw, workspace, port=0):
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    metadata = root / "browser.json"
    if metadata.exists():
        data = json.loads(metadata.read_text())
        try:
            with urlopen(local_endpoint(data["endpoint"]) + "/json/version", timeout=2):
                return data
        except OSError:
            pass
    if not port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    executable = Path(pw.chromium.executable_path)
    args = [
        "--user-data-dir=" + str(root / "browser-profile"),
        "--remote-debugging-port=" + str(port),
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    if sys.platform == "darwin":
        bundle = next((p for p in executable.parents if p.suffix == ".app"), None)
        if not bundle:
            raise RuntimeError(
                "Install Playwright Chromium before starting the background browser"
            )
        subprocess.run(["open", "-g", "-n", str(bundle), "--args"] + args, check=True)
    else:
        # Visible, detached browser. Focus policy is controlled by the desktop on Linux/Windows.
        subprocess.Popen(
            [str(executable)] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    data = {
        "endpoint": "http://127.0.0.1:" + str(port),
        "profile": str(root / "browser-profile"),
    }
    for _ in range(50):
        try:
            with urlopen(data["endpoint"] + "/json/version", timeout=1):
                metadata.write_text(json.dumps(data, indent=2) + "\n")
                return data
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(
        "Background Chromium did not expose CDP; check installation and port"
    )


def target_id(context, page):
    session = context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def page_for(context, target=None):
    if target:
        for page in context.pages:
            if target_id(context, page) == target:
                return page
    session = context.browser.new_browser_cdp_session()
    try:
        created = session.send(
            "Target.createTarget", {"url": "about:blank", "background": True}
        )["targetId"]
    finally:
        session.detach()
    for _ in range(40):
        for page in context.pages:
            if target_id(context, page) == created:
                return page
        time.sleep(0.05)
    raise RuntimeError("New background tab was not available")
