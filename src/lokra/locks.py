"""File locks for state that several Lokra processes share, on Linux, macOS and Windows."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def hold(path: Path, shared: bool = False):
    """Lock `path` until the block ends. Windows has no shared locks, so there every lock is exclusive."""
    with open(path, "a+b") as f:
        if os.name == "nt":
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def ledger_lock(ledger: Path) -> Path:
    """Earlier versions lock the ledger file itself, so keep that where locks are advisory. A Windows lock
    stops other processes reading the locked bytes, so there it lives in a file of its own."""
    return ledger.with_name(ledger.name + ".lock") if os.name == "nt" else ledger
