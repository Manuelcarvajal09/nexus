"""One bounded offer: wl-paste supplies state and bytes from the same offer.

Invoked as the --watch child for an explicitly requested MIME. wl-clipboard
2.3 has CLIPBOARD_STATE but no CLIPBOARD_TYPE, so the exact requested MIME is
passed as an argument. Sensitive/unknown states never read stdin.
"""
import json
import os
import selectors
import signal
import subprocess
import sys
import time

MAX_BYTES = 16 * 1024 * 1024


def write_offer(state, mime, source, target):
    raw = b''
    if state == 'data':
        raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            state, raw = 'oversize', b''
    header = json.dumps({'state': state, 'mime': mime, 'size': len(raw)}).encode() + b'\n'
    target.write(header)
    target.write(raw)
    target.flush()


def read_offer(wl_paste, mime, timeout=2):
    """Use a short-lived watch to receive the initial offer, then terminate it.

    The MIME list is only a selection hint, never authorization to store bytes.
    A compositor change between list-types and this connection is safe: the
    initial offer carries its own sensitive state. No clipboard write occurs.
    """
    command = [wl_paste, '--no-newline', '--type', mime, '--watch',
               sys.executable, '-m', 'nexus_linux.clipboard_offer', mime]
    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        received = bytearray()
        header = None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline-time.monotonic())):
                    return None
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    return None
                received.extend(chunk)
                if header is None:
                    boundary = received.find(b'\n')
                    if boundary < 0:
                        if len(received) > 1024:
                            return None
                        continue
                    if boundary > 1024:
                        return None
                    header = json.loads(received[:boundary])
                    del received[:boundary+1]
                    if (not isinstance(header, dict) or header.get('mime') != mime
                            or type(header.get('size')) is not int
                            or not 0 <= header['size'] <= MAX_BYTES):
                        return None
                    if header.get('state') != 'data':
                        return header.get('state'), mime, None
                if len(received) >= header['size']:
                    return 'data', mime, bytes(received[:header['size']])
        return None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if process is not None:
            # Also reap the child if an application stalled the transfer.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=.2)
            process.stdout.close()


if __name__ == '__main__':
    write_offer(os.environ.get('CLIPBOARD_STATE', 'unknown'), sys.argv[1],
                sys.stdin.buffer, sys.stdout.buffer)
