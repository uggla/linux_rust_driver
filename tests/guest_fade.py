#!/usr/bin/env python3
"""Run inside the stage-06 VM (root); invoked by make check-offloading."""
import errno
import fcntl
import glob
import os
import signal
import struct
import threading
import time

START = (1 << 30) | (8 << 16) | (ord('r') << 8) | 2
WAIT = (ord('r') << 8) | 3

def expect(code, call):
    try:
        call()
    except OSError as error:
        assert error.errno == code, (error, code)
    else:
        raise AssertionError(f'expected errno {code}')

def start(fd, color=0xff0000, duration=300):
    fcntl.ioctl(fd, START, struct.pack('=II', color, duration))

def wait(fd):
    fcntl.ioctl(fd, WAIT)

def irq_count():
    for line in open('/proc/interrupts'):
        if line.split()[-1:] == ['rgb']:
            return sum(int(word) for word in line.split()[1:] if word.isdigit())
    raise AssertionError('missing rgb IRQ')

def devmem(address, value=None):
    command = f'devmem {address:#x} 32'
    if value is not None:
        command += f' {value}'
    with os.popen(command) as output:
        result = output.read().strip()
    return int(result, 16) if value is None else None

fd = os.open('/dev/rgb', os.O_RDWR)
expect(errno.EINVAL, lambda: wait(fd))
for color, duration in [(0x1000000, 1), (0, 0), (0, 60001)]:
    expect(errno.EINVAL, lambda: start(fd, color, duration))
expect(errno.ENOTTY, lambda: fcntl.ioctl(fd, (ord('r') << 8) | 1))
expect(errno.EFAULT, lambda: fcntl.ioctl(fd, START, 0))
initial = irq_count()
begin = time.monotonic()
start(fd, duration=1000)
assert time.monotonic() - begin < 0.5, 'START blocked'
expect(errno.EBUSY, lambda: start(fd))
expect(errno.EBUSY, lambda: os.write(fd, b'00FF00'))
results = []
def waiter():
    wait(fd)
    results.append(True)
threads = [threading.Thread(target=waiter) for _ in range(2)]
for thread in threads:
    thread.start()
time.sleep(0.1)
for thread in threads:
    status = open(f'/proc/self/task/{thread.native_id}/status').read()
    assert 'State:\tS (sleeping)' in status, status
for thread in threads:
    thread.join(5)
assert len(results) == 2
assert time.monotonic() - begin >= 0.8
assert irq_count() == initial + 1
assert devmem(0x090d0000) == 255
assert devmem(0x090d0004) == 0
assert devmem(0x090d0008) == 0
assert devmem(0x090d0020) == 0
begin = time.monotonic()
wait(fd)
assert time.monotonic() - begin < 0.5
for target in (0x00ff00, 0x0000ff):
    start(fd, target, 100)
    wait(fd)
assert irq_count() == initial + 3

def interrupt(_sig, _frame):
    raise InterruptedError('test signal')
signal.signal(signal.SIGALRM, interrupt)
start(fd, duration=2000)
signal.setitimer(signal.ITIMER_REAL, 0.1)
try:
    wait(fd)
except InterruptedError:
    pass
else:
    raise AssertionError('WAIT did not respond to signal')
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
wait(fd)

# Mask the device output: hardware finishes, but WAIT must not poll STATUS.
start(fd, duration=1000)
devmem(0x090d0024, 0)
expect(errno.ETIMEDOUT, lambda: wait(fd))
assert devmem(0x090d0020) == 1
devmem(0x090d0024, 1)
wait(fd)

# An open file survives unbind safely, and an existing waiter gets ENODEV.
start(fd, duration=10000)
removed = []
def removed_waiter():
    expect(errno.ENODEV, lambda: wait(fd))
    removed.append(True)
thread = threading.Thread(target=removed_waiter)
thread.start()
time.sleep(0.1)
driver = '/sys/bus/platform/drivers/rgb/'
node = next(path for path in glob.glob(driver + '*') if 'rgb-led' in path)
name = os.path.basename(node)
with open(driver + 'unbind', 'w') as control:
    control.write(name)
thread.join(5)
assert removed == [True]
expect(errno.ENODEV, lambda: start(fd))
os.close(fd)
with open(driver + 'bind', 'w') as control:
    control.write(name)
os.system('rm /dev/rgb; major=$(awk \'$2 == "rgb" { print $1 }\' /proc/devices); mknod /dev/rgb c "$major" 0')
fd = os.open('/dev/rgb', os.O_RDWR)
start(fd, duration=100)
wait(fd)
os.close(fd)
assert os.system('rmmod rgb && modprobe rgb') == 0
print('fade/IRQ VM scenarios: OK', flush=True)
