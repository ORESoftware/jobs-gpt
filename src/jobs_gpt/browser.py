"""OS-owned Chromium: controller exit preserves unresolved tabs."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .network import open_no_redirect
from .privacy import private_directory, write_private


def local_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not parsed.port
    ):
        raise ValueError("CDP endpoint must be an HTTP loopback address")
    return endpoint.rstrip("/")


def version(endpoint):
    endpoint = local_endpoint(endpoint)
    with open_no_redirect(endpoint + "/json/version", timeout=2) as response:
        info = json.load(response)
    ws = urlparse(info.get("webSocketDebuggerUrl", ""))
    http = urlparse(endpoint)
    if (
        ws.scheme != "ws"
        or ws.hostname not in ("127.0.0.1", "localhost")
        or ws.port != http.port
        or ws.username
        or ws.password
    ):
        raise ValueError("CDP returned an unexpected debugging socket")
    return info


def start(pw, workspace, port=0):
    root = private_directory(workspace)
    metadata = root / "browser.json"
    if metadata.exists():
        data = json.loads(metadata.read_text())
        if (
            Path(data.get("profile", "")).resolve()
            != (root / "browser-profile").resolve()
        ):
            raise RuntimeError("Browser metadata belongs to a different workspace")
        try:
            info = version(data["endpoint"])
        except OSError:
            info = None
        if info:
            if (
                not data.get("websocket")
                or data["websocket"] != info["webSocketDebuggerUrl"]
            ):
                raise RuntimeError(
                    "Browser endpoint identity changed; inspect the browser before explicit attachment"
                )
            return data
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
            info = version(data["endpoint"])
            data["websocket"] = info["webSocketDebuggerUrl"]
            write_private(metadata, data)
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
