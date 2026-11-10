"""Protocol, transport and headless rendering checks for the LED viewer."""
import importlib.util
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('rgb_viewer', Path(__file__).resolve().parents[1] / 'tools/rgb_viewer.py')
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class ViewerTests(unittest.TestCase):
    def test_stream_boundaries_and_invalid_lines(self):
        stream = viewer.ColorStream()
        self.assertEqual(stream.feed(b'FF00'), [])
        self.assertEqual(stream.feed(b'00\ninvalid\n00FF00\n000'), [(255, 0, 0), (0, 255, 0)])
        self.assertEqual(stream.feed(b'000\n'), [viewer.BLACK])
        with self.assertRaises(ValueError):
            stream.feed(b'x' * 4097)
        with self.assertRaises(ValueError):
            viewer.ColorStream().feed(b'x' * 4097 + b'\n')

    def wait_update(self, connection, expected):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if connection.poll() == expected:
                return
            time.sleep(0.01)
        self.fail(f'missing update {expected}')

    def exercise_transport(self, server, address, tcp):
        connection = viewer.Connection(address, tcp)
        server.settimeout(4)
        connection.thread.start()
        try:
            with server.accept()[0] as peer:
                peer.sendall(b'12')
                peer.sendall(b'3456\n')
                self.wait_update(connection, (True, (18, 52, 86)))
            self.wait_update(connection, (False, viewer.BLACK))
            with server.accept()[0] as peer:
                # A new connection starts with a fresh protocol buffer.
                peer.sendall(b'0000FF\n')
                self.wait_update(connection, (True, (0, 0, 255)))
            self.wait_update(connection, (False, viewer.BLACK))
            with server.accept()[0] as peer:
                peer.sendall(b'x' * 4097)
                self.wait_update(connection, (False, viewer.BLACK))
        finally:
            connection.close()
        self.assertFalse(connection.thread.is_alive())

    def test_unix_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'led.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen()
                self.exercise_transport(server, path, False)

    def test_tcp_reconnect(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            self.exercise_transport(server, server.getsockname(), True)

    def test_rendering_and_resize(self):
        os.environ['SDL_VIDEODRIVER'] = 'dummy'
        import pygame
        pygame.display.init()
        pygame.font.init()
        try:
            font = pygame.font.Font(None, 22)
            for size in [(480, 480), (800, 300), (200, 600), (30, 30)]:
                screen = pygame.display.set_mode(size, pygame.RESIZABLE)
                viewer.draw(screen, font, (255, 0, 0), True, 'test.sock')
                # Interior pixels confirm the LED color, including after resize.
                center = (size[0] // 2, max(1, size[1] - 90) // 2)
                if min(size) > 30:
                    self.assertEqual(tuple(screen.get_at(center))[:3], (255, 0, 0))
                viewer.draw(screen, font, viewer.BLACK, False, 'test.sock')
                if min(size) > 30:
                    self.assertEqual(tuple(screen.get_at(center))[:3], viewer.BLACK)
        finally:
            pygame.quit()


if __name__ == '__main__':
    unittest.main()
