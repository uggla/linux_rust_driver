#!/usr/bin/env python3
"""Boot the generated image and exercise one RGB MMIO write."""

from __future__ import annotations

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
    if len(sys.argv) != 4:
        raise SystemExit("usage: smoke_vm.py QEMU KERNEL ROOTFS")

    qemu, kernel, rootfs = map(Path, sys.argv[1:])
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
        wait_for(process, output, b"rgb-led: apply RGB(255, 0, 0)", COMMAND_TIMEOUT)

        required = (
            b"platform device matched",
            b"requested color r=255 g=0 b=0",
            b"wrote MMIO color r=255 g=0 b=0",
        )
        missing = [item for item in required if item not in output]
        if missing:
            fail(f"missing expected log entries: {missing!r}", output)
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
