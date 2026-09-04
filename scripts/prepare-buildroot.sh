#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_repo="$root/vendor/buildroot"
worktree="$root/.build/src/buildroot"
pin=6b1f6f7a48

mkdir -p "$root/.build/src"
if [ ! -d "$worktree/.git" ]; then
	git clone --shared "$source_repo" "$worktree"
fi

git -C "$worktree" reset --hard "$pin"
git -C "$worktree" clean -ffd
git -C "$worktree" apply "$root/integrations/buildroot/0001-buildroot-support-Rust-enabled-Linux-6.18.patch"
