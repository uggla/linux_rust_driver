#!/usr/bin/env python3
"""Boot a selected driver stage and validate its supported interfaces."""

from __future__ import annotations

import argparse
import base64
import os
import select
import subprocess
import sys
import time
from pathlib import Path


BOOT_TIMEOUT = 120
COMMAND_TIMEOUT = 30


def fail(message: str, output: bytearray) -> "NoReturn":
    sys.stderr.write(f"smoke test failed: {message}\n")
    sys.stderr.write(output.decode(errors="replace"))
    raise SystemExit(1)


def wait_for(
    process: subprocess.Popen[bytes],
    output: bytearray,
    needle: bytes,
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    while needle not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail(f"timeout waiting for {needle!r}", output)
        ready, _, _ = select.select([process.stdout], [], [], min(1, remaining))
        if not ready:
            if process.poll() is not None:
                fail(f"QEMU exited with status {process.returncode}", output)
            continue
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            fail("QEMU closed its output", output)
        output.extend(chunk)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()


def send(process: subprocess.Popen[bytes], command: str) -> None:
    assert process.stdin is not None
    process.stdin.write(command.encode() + b"\n")
    process.stdin.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("rootfs", type=Path)
    parser.add_argument("guest_test", nargs="?", type=Path)
    parser.add_argument("--stage", default="06-hw-offloading", choices=(
        "01-character-device", "02-mmio-fixed", "03-device-tree",
        "04-ioctl-sync", "05-ioctl-async", "06-hw-offloading",
    ))
    parser.add_argument("--extended", action="store_true",
                        help="also run guest scenarios available for this stage")
    args = parser.parse_args()
    qemu, kernel, rootfs = args.qemu, args.kernel, args.rootfs
    guest_test = args.guest_test
    if guest_test is None and args.extended and args.stage == "06-hw-offloading":
        guest_test = Path(__file__).with_name("guest_fade.py")
    for artifact in (qemu, kernel, rootfs):
        if not artifact.is_file():
            raise SystemExit(f"missing build artifact: {artifact}")

    command = [
        "stdbuf",
        "-oL",
        "-eL",
        str(qemu),
        "-machine",
        "virt",
        "-cpu",
        "cortex-a57",
        "-m",
        "1G",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-no-reboot",
        "-snapshot",
        "-kernel",
        str(kernel),
        "-append",
        "console=ttyAMA0 root=/dev/vda rw",
        "-drive",
        f"file={rootfs},if=none,id=hd0,format=raw",
        "-device",
        "virtio-blk-device,drive=hd0",
    ]

    output = bytearray()
    environment = os.environ.copy()
    temporary_directory = rootfs.parent / ".qemu-tmp"
    temporary_directory.mkdir(exist_ok=True)
    environment["TMPDIR"] = str(temporary_directory)
    process = subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert process.stdout is not None

    try:
        wait_for(process, output, b"buildroot login:", BOOT_TIMEOUT)
        send(process, "root")
        wait_for(process, output, b"# ", COMMAND_TIMEOUT)

        send(process, "modprobe rgb; rc=$?; echo __RGB_MODPROBE_${rc}__")
        wait_for(process, output, b"__RGB_MODPROBE_0__", COMMAND_TIMEOUT)

        send(
            process,
            "major=$(awk '$2 == \"rgb\" { print $1 }' /proc/devices); "
            "test -n \"$major\" && mknod /dev/rgb c \"$major\" 0 && "
            "echo FF0000 > /dev/rgb; rc=$?; echo __RGB_WRITE_${rc}__",
        )
        wait_for(process, output, b"__RGB_WRITE_0__", COMMAND_TIMEOUT)
        required = [b"requested color r=255 g=0 b=0"]
        if args.stage != "01-character-device":
            wait_for(process, output, b"rgb-led: apply RGB(255, 0, 0)", COMMAND_TIMEOUT)
            required.append(b"wrote MMIO color r=255 g=0 b=0")
        if args.stage not in ("01-character-device", "02-mmio-fixed"):
            required.append(b"platform device matched")
        if args.stage == "01-character-device" and b"rgb-led: apply" in output:
            fail("character-only stage unexpectedly touched hardware", output)
        missing = [item for item in required if item not in output]
        if missing:
            fail(f"missing expected log entries: {missing!r}", output)
        if guest_test is not None:
            payload = base64.b64encode(guest_test.read_bytes()).decode()
            send(process, ": > /tmp/rgb-test.b64")
            for offset in range(0, len(payload), 512):
                send(process, "echo '" + payload[offset:offset + 512] + "' >> /tmp/rgb-test.b64")
            send(process, "base64 -d /tmp/rgb-test.b64 > /tmp/rgb-test.py; python3 /tmp/rgb-test.py; rc=$?; echo __RGB_EXTRA_${rc}__")
            wait_for(process, output, b"__RGB_EXTRA_0__", 150)
        print("RGB VM smoke test: OK")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
