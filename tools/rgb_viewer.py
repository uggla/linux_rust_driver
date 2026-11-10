#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["pygame>=2.6,<3"]
# ///
"""Observe QEMU's RGB LED over a Unix socket or a socat TCP relay."""
import argparse
import queue
import re
import socket
import threading
from pathlib import Path

BLACK = (0, 0, 0)
DEFAULT_SOCKET = Path(__file__).resolve().parents[1] / ".build" / "rgb-led.sock"


class ColorStream:
    """Incrementally decode newline-delimited RRGGBB without unbounded storage."""
    def __init__(self):
        self.buffer = b""

    def feed(self, data):
        self.buffer += data
        colors = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if len(line) > 4096:
                raise ValueError("color line exceeds 4 KiB")
            if re.fullmatch(b"[0-9A-F]{6}", line):
                colors.append(tuple(bytes.fromhex(line.decode("ascii"))))
        if len(self.buffer) > 4096:
            raise ValueError("color line exceeds 4 KiB")
        return colors


def tcp_address(value):
    try:
        host, port = value.rsplit(":", 1)
        port = int(port)
        if not host or not 1 <= port <= 65535:
            raise ValueError
        return host.strip("[]"), port
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected HOST:PORT (port 1–65535)") from error


class Connection:
    """Keep networking, including DNS, away from the graphical event loop."""
    def __init__(self, address, tcp=False):
        self.address = address
        self.tcp = tcp
        self.updates = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def publish(self, connected, color):
        try:
            self.updates.get_nowait()
        except queue.Empty:
            pass
        self.updates.put_nowait((connected, color))

    def poll(self):
        try:
            return self.updates.get_nowait()
        except queue.Empty:
            return None

    def run(self):
        while not self.stop.is_set():
            sock = None
            try:
                if self.tcp:
                    sock = socket.create_connection(self.address, timeout=0.5)
                else:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    sock.connect(str(self.address))
                sock.settimeout(0.2)
                stream = ColorStream()
                color = BLACK
                self.publish(True, color)
                while not self.stop.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    colors = stream.feed(data)
                    if colors:
                        color = colors[-1]
                        self.publish(True, color)
            except (OSError, ValueError):
                pass
            finally:
                if sock is not None:
                    sock.close()
                self.publish(False, BLACK)
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)


def draw(screen, font, color, connected, endpoint):
    import pygame

    width, height = screen.get_size()
    screen.fill((35, 38, 42))
    # Reserve the bottom for labels; keep the LED circular at any aspect ratio.
    available = max(1, height - 90)
    radius = max(1, int(min(width, available) * 0.38))
    center = (width // 2, available // 2)
    pygame.draw.circle(screen, color, center, radius)
    pygame.draw.circle(screen, (180, 185, 190), center, radius, width=min(2, radius))
    labels = ["#%02X%02X%02X" % color,
              "Connected" if connected else "Disconnected — retrying…", endpoint]
    for index, text in enumerate(labels):
        surface = font.render(text, True, (225, 228, 232))
        screen.blit(surface, (max(4, (width - surface.get_width()) // 2), available + index * 25))


def main():
    import pygame

    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--unix", type=Path, default=DEFAULT_SOCKET)
    transport.add_argument("--tcp", type=tcp_address)
    args = parser.parse_args()
    address = args.tcp if args.tcp else args.unix
    endpoint = f"{address[0]}:{address[1]}" if args.tcp else str(address)
    connection = Connection(address, tcp=bool(args.tcp))
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((480, 480), pygame.RESIZABLE)
    pygame.display.set_caption("QEMU RGB LED")
    font = pygame.font.Font(None, 22)
    clock = pygame.time.Clock()
    connected, color = False, BLACK
    connection.thread.start()
    try:
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    running = False
            update = connection.poll()
            if update is not None:
                connected, color = update
            draw(screen, font, color, connected, endpoint)
            pygame.display.flip()
            clock.tick(60)
    finally:
        connection.close()
        pygame.quit()


if __name__ == "__main__":
    main()
