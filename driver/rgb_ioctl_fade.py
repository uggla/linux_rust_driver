#!/usr/bin/env python3
"""Submit a hardware fade, then sleep in WAIT until its completion IRQ."""
import argparse
import fcntl
import os
import struct
import time

RGB_IOCTL_FADE_START = (1 << 30) | (8 << 16) | (ord("r") << 8) | 2
RGB_IOCTL_FADE_WAIT = (ord("r") << 8) | 3

def color(value):
    digits = value.removeprefix("#")
    if len(digits) != 6:
        raise argparse.ArgumentTypeError("expected RRGGBB")
    try:
        return int(digits, 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected RRGGBB") from error

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", nargs="?", default="/dev/rgb")
    parser.add_argument("--color", type=color, default=0xff0000)
    parser.add_argument("--duration-ms", type=int, default=3000)
    args = parser.parse_args()
    if not 1 <= args.duration_ms <= 60000:
        parser.error("duration must be between 1 and 60000 ms")
    with open(args.device, "rb", buffering=0) as dev:
        print(f"PID: {os.getpid()}", flush=True)
        start = time.monotonic()
        fcntl.ioctl(dev, RGB_IOCTL_FADE_START, struct.pack("=II", args.color, args.duration_ms))
        print(f"START returned after {time.monotonic() - start:.3f}s", flush=True)
        print("Waiting for completion…", flush=True)
        fcntl.ioctl(dev, RGB_IOCTL_FADE_WAIT)
        print(f"WAIT returned — fade completed after {time.monotonic() - start:.3f}s", flush=True)

if __name__ == "__main__":
    main()
