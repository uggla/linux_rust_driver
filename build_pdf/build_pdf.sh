#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
url=http://127.0.0.1:8000/slides/

command -v podman >/dev/null 2>&1 || {
    echo "podman is required" >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    echo "curl is required" >&2
    exit 1
}
curl -fsS "$url" >/dev/null || {
    echo "slides are not served; run 'make slides-serve' first" >&2
    exit 1
}

podman run --network host --userns=keep-id --rm \
    -v "$root/slides:/data:Z" \
    localhost/rgb-driver-slides-pdf:latest
