#!/usr/bin/env python3

import argparse
import fcntl


RGB_IOCTL_CYCLE = (ord("r") << 8) | 0x01


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trigger the RGB driver's red/green/blue ioctl cycle.",
    )
    parser.add_argument(
        "device",
        nargs="?",
        default="/dev/rgb",
        help="RGB character device path, default: /dev/rgb",
    )
    args = parser.parse_args()

    with open(args.device, "rb", buffering=0) as dev:
        fcntl.ioctl(dev.fileno(), RGB_IOCTL_CYCLE, 0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
