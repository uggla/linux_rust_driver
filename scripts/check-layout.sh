#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

test "$(git -C "$root/vendor/qemu" rev-parse HEAD)" = "8f1d3b586f1265023f75ea9c227c35d463321aef"
test "$(git -C "$root/vendor/buildroot" rev-parse HEAD)" = "6b1f6f7a480765b3d6ca07691095035a8b899eaf"
test -z "$(git -C "$root/vendor/qemu" status --porcelain)"
test -z "$(git -C "$root/vendor/buildroot" status --porcelain)"

for stage in \
	01-character-device \
	02-mmio-fixed \
	03-device-tree \
	04-ioctl-sync \
	05-ioctl-async
do
	test -s "$root/driver/stages/$stage/rgb.rs"
done

test -s "$root/device/qemu/rgb-led.c"
echo "layout and submodule pins: OK"
