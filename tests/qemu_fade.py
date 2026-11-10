#!/usr/bin/env python3
"""QTest/QMP checks: interpolation, IRQ level, reset, snapshot restore."""
import json
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
            write(0x14, 0xff0000)
            write(0x18, 1000)
            write(0x24, 1)
            write(0x1c, 1)
            step(500)
            middle = read(0)
            assert 100 <= middle <= 150, middle
            # Snapshot records a running timer and interpolation origin.
            command('stop')
            result = command('human-monitor-command', {'command-line': 'savevm fade'})
            assert not result.strip(), result
            command('cont')
            step(500)
            assert read(0) == 255 and read(0x20) == 1
            assert any(line == 'IRQ raise 0' for line in levels), levels
            write(0x20, 1)
            assert read(0x20) == 0
            assert 'IRQ lower 0' in levels
            command('stop')
            result = command('human-monitor-command', {'command-line': 'loadvm fade'})
            assert not result.strip(), result
            assert read(0x10) == 0
            assert read(0) == middle
            command('cont')
            # Restore VMState + timer; advancing virtual time finishes once.
            step(1000)
            assert read(0) == 255 and read(0x20) == 1
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
            print('QEMU fade/reset/VMState checks: OK')
        except Exception:
            print((temp / 'qemu.log').read_text(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            process.wait(timeout=10)
