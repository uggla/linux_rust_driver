#!/usr/bin/env python3
"""QTest/QMP checks: interpolation, IRQ level, reset, snapshot restore."""
import json
import re
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

BASE = 0x090d0000

def connect(path, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('QEMU exited before opening test socket')
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(str(path))
            sock.settimeout(10)
            return sock.makefile('rwb', buffering=0)
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            time.sleep(0.02)
    raise TimeoutError(path)

with tempfile.TemporaryDirectory() as temp:
    temp = pathlib.Path(temp)
    disk = temp / 'state.qcow2'
    subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', str(disk), '16M'], check=True)
    with open(temp / 'qemu.log', 'wb') as log:
        process = subprocess.Popen([
            sys.argv[1], '-machine', 'virt', '-accel', 'qtest', '-display', 'none',
            '-qtest', f'unix:{temp}/qtest,server=on,wait=off',
            '-qmp', f'unix:{temp}/qmp,server=on,wait=off',
            '-drive', f'file={disk},if=none,id=state,format=qcow2',
            '-device', 'virtio-blk-device,drive=state',
            '-chardev', f'socket,id=led,path={temp}/led,server=on,wait=off',
            '-global', 'rgb-led.chardev=led',
        ], stdout=log, stderr=log)
        try:
            qtest = connect(temp / 'qtest', process)
            qmp = connect(temp / 'qmp', process)
            assert 'QMP' in json.loads(qmp.readline())
            def command(name, arguments=None):
                qmp.write(json.dumps({'execute': name, 'arguments': arguments or {}}).encode() + b'\n')
                while True:
                    reply = json.loads(qmp.readline())
                    if 'event' in reply:
                        continue
                    assert 'error' not in reply, reply
                    return reply['return']
            command('qmp_capabilities')
            led = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            led.settimeout(5)
            led.connect(str(temp / 'led'))
            led_buffer = b''
            def color_line():
                global led_buffer
                while b'\n' not in led_buffer:
                    chunk = led.recv(4096)
                    assert chunk, 'LED stream closed'
                    led_buffer += chunk
                line, led_buffer = led_buffer.split(b'\n', 1)
                assert re.fullmatch(b'[0-9A-F]{6}', line), line
                return line
            def until_color(expected):
                for _ in range(10000):
                    if color_line() == expected:
                        return
                raise AssertionError(f'missing color {expected}')
            assert color_line() == b'000000'
            levels = []
            def qt(line):
                qtest.write(line.encode() + b'\n')
                while True:
                    answer = qtest.readline().decode().strip()
                    if answer.startswith('IRQ '):
                        levels.append(answer)
                        continue
                    assert answer.startswith('OK'), answer
                    return answer.split()[1:]
            def write(offset, value):
                qt(f'writel {BASE + offset:#x} {value:#x}')
            def read(offset):
                return int(qt(f'readl {BASE + offset:#x}')[0], 0)
            def step(ms):
                qt(f'clock_step {ms * 1000000}')
            # SysBus exports a named GPIO group, not an unnamed output.
            def find_rgb(path='/machine'):
                for item in command('qom-list', {'path': path}):
                    if item['type'].startswith('child<'):
                        child = path + '/' + item['name']
                        if item['type'] == 'child<rgb-led>':
                            return child
                        found = find_rgb(child)
                        if found:
                            return found
                return None
            device = find_rgb()
            assert device
            qt(f'irq_intercept_out {device} sysbus-irq')
            write(0x00, 255)
            write(0x0c, 1)
            assert color_line() == b'FF0000'
            write(0x14, 0)
            write(0x18, 1000)
            write(0x24, 1)
            write(0x1c, 1)
            step(500)
            middle = read(0)
            until_color(f'{middle:02X}0000'.encode())
            assert 100 <= middle <= 150, middle
            # Snapshot records a running timer and interpolation origin.
            command('stop')
            result = command('human-monitor-command', {'command-line': 'savevm fade'})
            assert not result.strip(), result
            command('cont')
            step(500)
            assert read(0) == 0 and read(0x20) == 1
            until_color(b'000000')
            assert any(line == 'IRQ raise 0' for line in levels), levels
            write(0x20, 1)
            assert read(0x20) == 0
            assert 'IRQ lower 0' in levels
            command('stop')
            result = command('human-monitor-command', {'command-line': 'loadvm fade'})
            assert not result.strip(), result
            assert read(0x10) == 0
            assert read(0) == middle
            until_color(f'{middle:02X}0000'.encode())
            command('cont')
            # Restore VMState + timer; advancing virtual time finishes once.
            step(1000)
            assert read(0) == 0 and read(0x20) == 1
            write(0x20, 1)
            write(0x14, 0xff)
            write(0x1c, 1)
            step(100)
            write(0x1c, 2)
            cancelled = read(0)
            step(2000)
            assert read(0) == cancelled and read(0x20) == 0
            write(0x1c, 1)
            command('system_reset')
            step(2000)
            assert read(0) == 0 and read(0x10) == 1 and read(0x20) == 0
            until_color(b'000000')
            # A viewer that stops reading must never stall the device or IRQ.
            for _ in range(3000):
                write(0x0c, 1)
            write(0x14, 0xff)
            write(0x18, 100)
            write(0x24, 1)
            write(0x1c, 1)
            step(100)
            assert read(0x20) == 1, 'slow viewer stalled completion IRQ'
            write(0x20, 1)
            write(0, 0x12)
            write(4, 0x34)
            write(8, 0x56)
            write(0x0c, 1)
            until_color(b'123456')
            led.close()
            # No viewer attached: hardware must still finish and raise its IRQ.
            write(0x14, 0)
            write(0x18, 100)
            write(0x24, 1)
            write(0x1c, 1)
            step(100)
            assert read(0x20) == 1
            led = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            led.settimeout(5)
            led.connect(str(temp / 'led'))
            led_buffer = b''
            assert color_line() == b'000000'
            led.close()
            print('QEMU socket/color/reconnection checks: OK')
            print('QEMU fade/reset/VMState checks: OK')
        except Exception:
            print((temp / 'qemu.log').read_text(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            process.wait(timeout=10)
