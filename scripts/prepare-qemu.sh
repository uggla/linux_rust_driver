#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_repo="$root/vendor/qemu"
worktree="$root/.build/src/qemu"
pin=8f1d3b586f

mkdir -p "$root/.build/src"
if [ ! -d "$worktree/.git" ]; then
	git clone --shared "$source_repo" "$worktree"
fi

git -C "$worktree" reset --hard "$pin"
git -C "$worktree" clean -ffd
git -C "$worktree" apply "$root/integrations/qemu/0001-hw-arm-wire-the-RGB-LED-device-into-virt.patch"
install -D -m 0644 "$root/device/qemu/rgb-led.c" "$worktree/hw/misc/rgb-led.c"
