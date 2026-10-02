from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from antigravity_cli_switcher.manager.paths import ManagerPaths, ensure_layout


@contextmanager
def manager_lock(paths: ManagerPaths) -> Iterator[None]:
    ensure_layout(paths)
    with paths.lock_file.open("a+", encoding="utf-8") as f:
        if sys.platform == "win32":
            f.seek(0)
            f.write("0")
            f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(str(os.getpid()))
            f.flush()
            yield
        finally:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    f.write("0")
                    f.truncate(1)
                    f.flush()
                else:
                    f.truncate()
            finally:
                if sys.platform == "win32":
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
