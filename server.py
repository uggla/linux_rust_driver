#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["staticjinja"]
# ///
"""Build and serve the Reveal.js presentation."""

import argparse
import http.server
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
SLIDES = ROOT / "slides"
HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8000"))


class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def check_tools() -> None:
    if shutil.which("staticjinja") is None:
        raise SystemExit("staticjinja is unavailable; run this script through uv")
    if not (ROOT / "reveal.js" / "package.json").is_file():
        raise SystemExit("reveal.js is missing; run: git submodule update --init")


def build() -> None:
    print("Building the presentation")
    subprocess.run(["staticjinja", "build"], cwd=SLIDES, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="rebuild on changes")
    parser.add_argument("--build-only", action="store_true", help="build and exit")
    args = parser.parse_args()

    os.chdir(ROOT)
    check_tools()
    build()
    if args.build_only:
        return 0

    watcher = None
    if args.watch:
        watcher = subprocess.Popen(["staticjinja", "watch"], cwd=SLIDES)

    try:
        handler = http.server.SimpleHTTPRequestHandler
        with ReuseTCPServer((HOST, PORT), handler) as server:
            print(f"Serving at http://{HOST}:{PORT}/slides/")
            server.serve_forever()
    finally:
        if watcher is not None:
            watcher.terminate()
            watcher.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
