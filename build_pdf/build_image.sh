#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

command -v podman >/dev/null 2>&1 || {
    echo "podman is required" >&2
    exit 1
}

podman build -t localhost/rgb-driver-slides-pdf:latest "$root"
