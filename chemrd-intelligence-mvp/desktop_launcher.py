"""Double-click launcher for the ChemR&D local desktop build."""

from __future__ import annotations

import multiprocessing
import os
import socket
import threading
import time
from pathlib import Path

from dotenv import load_dotenv


def _available_port(preferred: int = 8899) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No local TCP port is available")


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError("ChemR&D server did not start in time")


class DesktopApi:
    """Bridge exposed to the SPA for opening cited pages in native windows."""

    def open_source(self, url: str, title: str = "Source") -> dict[str, str]:
        from urllib.parse import urlparse
        import webview

        parsed = urlparse(url or "")
        if parsed.scheme not in {"http", "https"}:
            return {"status": "rejected", "reason": "Only http and https sources are allowed"}
        webview.create_window(
            title=f"ChemR&D · {title[:70]}",
            url=url,
            width=1280,
            height=850,
            min_size=(900, 600),
            resizable=True,
        )
        return {"status": "opened"}


def main() -> None:
    executable_dir = Path(__file__).resolve().parent
    if getattr(__import__("sys"), "frozen", False):
        executable_dir = Path(__import__("sys").executable).resolve().parent
    load_dotenv(executable_dir / ".env")

    # Keep the user database outside the temporary PyInstaller extraction
    # directory so it survives upgrades and remains writable.
    data_dir = Path(os.getenv("LOCALAPPDATA", Path.home())) / "ChemRD"
    data_dir.mkdir(parents=True, exist_ok=True)
    if getattr(__import__("sys"), "frozen", False):
        # A packaged app must not inherit a relative or development database
        # URL from the user's shell/.env. Keep the standalone data writable and
        # persistent under %LOCALAPPDATA%.
        os.environ["DATABASE_URL"] = f"sqlite:///{(data_dir / 'chemrd.db').as_posix()}"
    else:
        os.environ.setdefault("DATABASE_URL", f"sqlite:///{data_dir / 'chemrd.db'}")
    # A distributed app starts as a clean workspace. Demo records require an
    # explicit CHEMRD_DEMO_MODE=true, even if an older .env still has the old
    # CHEMRD_SEED=true setting.
    if os.getenv("CHEMRD_DEMO_MODE", "false").lower() in {"1", "true", "yes"}:
        os.environ["CHEMRD_SEED"] = "true"
        os.environ["CHEMRD_CLEAR_SEED_DATA"] = "false"
    else:
        os.environ["CHEMRD_SEED"] = "false"
        os.environ["CHEMRD_CLEAR_SEED_DATA"] = "true"

    from backend.app.main import app
    import uvicorn
    import webview

    port = _available_port()
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level=None, access_log=False)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    _wait_for_server(port)

    # Render the SPA in a native WebView2 window. No Chrome/Edge tab is opened.
    webview.create_window(
        "ChemR&D Intelligence",
        url,
        js_api=DesktopApi(),
        width=1440,
        height=900,
        min_size=(1060, 680),
        resizable=True,
    )
    webview.start(debug=False)
    server.should_exit = True
    server_thread.join(timeout=3)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
