"""Desktop entry point: local server + browser window in one executable.

PyInstaller bundles this into ``ExtractorConsole.exe``. On launch it:
  1. picks a free localhost port,
  2. starts uvicorn (the FastAPI app incl. UI + restructure endpoints),
  3. opens the default browser at the app,
  4. shuts the server down when the console window is closed.

Env vars (EXTRACT_MODEL, provider keys, ...) can be set via a ``.env`` file
next to the exe or system-wide — same configuration as the server deployment.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bootstrap_path() -> None:
    """Make ``extractor`` importable in both dev and frozen (PyInstaller) runs."""
    if getattr(sys, "frozen", False):
        return  # PyInstaller onedir: extractor ships as a normal package
    src = str(Path(__file__).resolve().parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    _bootstrap_path()

    # EXTRACT_PORT pins the port (useful for CI); EXTRACT_DESKTOP_NO_BROWSER=1
    # skips auto-opening the browser.
    port = int(os.environ.get("EXTRACT_PORT") or 0) or _free_port()
    url = f"http://127.0.0.1:{port}/"

    import uvicorn

    from extractor.api.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        print("server failed to start", file=sys.stderr)
        return 1

    no_browser = os.environ.get("EXTRACT_DESKTOP_NO_BROWSER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not no_browser:
        webbrowser.open(url)
    print(f"Extractor Console running at {url}  (close this window to quit)")

    try:
        while True:
            time.sleep(0.5)
            if not thread.is_alive():
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
