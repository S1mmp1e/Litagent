"""Start or open the configured local service without installing packages."""

import argparse
import json
import os
import socket
import threading
import urllib.request
import webbrowser

from lit_agent.settings import ROOT, library_identity
from scripts.maintenance import doctor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    port = int(os.getenv("LIT_PORT", "7860"))
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
            health = json.load(response)
        if health.get("application") != "litagent" or health.get("library_id") != library_identity():
            raise SystemExit(f"Port {port} is occupied by an old service or a different library. Stop it before starting LitAgent.")
        print(f"LitAgent is running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    except (OSError, ValueError):
        pass
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Port {port} is occupied. Stop the old service before starting LitAgent.")
    report = doctor()
    required = ("fastapi", "uvicorn", "sqlalchemy", "pypdf", "qdrant_client")
    if not all(report["packages"][name] for name in required):
        raise SystemExit("Missing dependencies. Run .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    print(f"Workspace: {ROOT}\nDatabase: {report['database']}\nURL: {url}")
    print("Model key: configured (not validated)" if report["key_present"] else "Model key: missing")
    print("Keep this window open. Ctrl+C stops the server.")
    if not args.no_browser:
        def open_when_ready():
            for _ in range(40):
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1):
                        webbrowser.open(url)
                        return
                except OSError:
                    threading.Event().wait(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    from lit_agent.webui import main as serve
    serve()


if __name__ == "__main__":
    main()
