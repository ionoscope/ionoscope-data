"""Coordinate GIRO pacing and foreground priority across collector processes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from uuid import uuid4


@contextmanager
def locked_state(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'gate.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            path = directory/'state.json'
            state = json.loads(path.read_text()) if path.exists() else {}
            yield state
            temporary = directory/'state.tmp'
            temporary.write_text(json.dumps(state), encoding='utf-8')
            temporary.replace(path)
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def directory():
    value = os.getenv('IONOSCOPE_GIRO_RATE_DIR')
    return Path(value) if value else None


def claim_slot(state, now, background=False):
    """Return a delay, or reserve the next slot when the request may start."""
    if background and state.get('priority_until', 0) > now:
        return min(5.0, state['priority_until']-now)
    delay = max(state.get('next_request', 0), state.get('blocked_until', 0))-now
    if delay > 0:
        return delay
    state['next_request'] = now+6.0
    return 0.0


def wait_for_slot():
    root = directory()
    if root is None:
        return
    while True:
        with locked_state(root) as state:
            delay = claim_slot(state, time.time(), os.getenv('IONOSCOPE_GIRO_BACKGROUND') == '1')
        if delay <= 0:
            return
        # Release the file lock while waiting so foreground work can proceed.
        time.sleep(min(delay, 5.0))


def cooldown(seconds):
    root = directory()
    if root is not None:
        with locked_state(root) as state:
            state['blocked_until'] = max(state.get('blocked_until', 0), time.time()+seconds)


def release_priority():
    """Collection children release network priority before CPU-only preparation."""
    root = directory()
    token = os.getenv('IONOSCOPE_GIRO_FOREGROUND_TOKEN')
    if root is not None and token:
        with locked_state(root) as state:
            if state.get('priority_owner') == token:
                state.update(priority_until=0, priority_owner=None)


@contextmanager
def foreground(timeout):
    root = directory()
    token = uuid4().hex
    previous = os.environ.get('IONOSCOPE_GIRO_FOREGROUND_TOKEN')
    os.environ['IONOSCOPE_GIRO_FOREGROUND_TOKEN'] = token
    if root is not None:
        with locked_state(root) as state:
            state.update(priority_owner=token, priority_until=time.time()+timeout)
    try:
        yield
    finally:
        try:
            release_priority()
        finally:
            if previous is None:
                os.environ.pop('IONOSCOPE_GIRO_FOREGROUND_TOKEN', None)
            else:
                os.environ['IONOSCOPE_GIRO_FOREGROUND_TOKEN'] = previous
