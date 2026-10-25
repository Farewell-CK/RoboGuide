"""Retain one actual provider writer until all managed SQLite connections close."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

DATABASES = (
    "memory.sqlite3",
    "memory-use.sqlite3",
    "file-jobs.sqlite3",
    "active-map/active-map.sqlite3",
)
WRITER_SCHEMA = "roboguide.memory-provider-writer/v0.1"
SNAPSHOT_WRITER_SCHEMA = "roboguide.memory-provider-writer/v0.2"
SCOPED_MAP_WRITER_SCHEMA = "roboguide.memory-provider-writer/v0.3"


class ProviderConnection(sqlite3.Connection):
    """Release storage ownership only after the actual SQLite connection has closed."""

    _provider_release: Callable[[], None] | None = None
    _provider_close_lock: threading.RLock | None = None

    def close(self) -> None:
        """Close SQLite before releasing its storage reference, including duplicate closes."""
        lock = self._provider_close_lock
        if lock is None:
            super().close()
            return
        with lock:
            super().close()
            release, self._provider_release = self._provider_release, None
            if release is not None:
                release()

    def __del__(self) -> None:
        """Close an abandoned connection; shutdown errors cannot grant another writer."""
        try:
            self.close()
        except BaseException:
            pass


class ProviderStorage:
    """Own root plus inode locks across all actual local provider database connections."""

    def __init__(self, root: Path) -> None:
        """Acquire all managed real-file locks before any SQLite initialization or WAL effect."""
        self.root = root
        self._mutex = threading.RLock()
        self._descriptors: dict[str, int] = {}
        self._identities: dict[str, tuple[int, int]] = {}
        self._active: set[str] = set()
        self._accepting = True
        try:
            for name in ("writer.lock", ".memory-use.lock", *DATABASES):
                path = root / name
                if path.parent != root:
                    path.parent.mkdir(mode=0o700, exist_ok=True)
                    metadata = path.parent.lstat()
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise ValueError(
                            "provider managed database parent must be a real directory"
                        )
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                self._descriptors[name] = descriptor
                metadata = os.fstat(descriptor)
                identity = (metadata.st_dev, metadata.st_ino)
                if not stat.S_ISREG(metadata.st_mode) or identity in self._identities.values():
                    raise ValueError("provider managed files must have distinct regular inodes")
                self._identities[name] = identity
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if name == "memory.sqlite3":
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as probe:
                        version = probe.execute("PRAGMA user_version").fetchone()[0]
                    if version in (3, 4, 5) and any(
                        not (root / child).exists() for child in DATABASES
                    ):
                        raise ValueError("original provider managed database is missing")
            self._verify()
            self._quiesce()
        except BaseException:
            self._unlock()
            raise

    def _verify(self) -> None:
        """Require the originally locked inode at every managed path before opening a connection."""
        for name, identity in self._identities.items():
            metadata = (self.root / name).lstat()
            if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
                raise ValueError("original provider database path or inode changed")

    def _quiesce(self) -> None:
        """Use SQLite's own exclusive journal transition to reject every still-open legacy alias."""
        for name in DATABASES:
            connection = sqlite3.connect(self.root / name, timeout=0.1)
            try:
                if connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                    raise ValueError("provider database did not become quiescent")
            finally:
                connection.close()

    def _unlock(self) -> None:
        """Unlock owned descriptors after the final connection or failed initialization."""
        for descriptor in reversed(tuple(self._descriptors.values())):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        self._descriptors.clear()
        self._accepting = False

    def _release(self, name: str) -> None:
        """Retain every physical lock while any sibling connection can still write."""
        with self._mutex:
            self._active.remove(name)
            if name == "memory.sqlite3":
                self._accepting = False
            if not self._active:
                self._unlock()

    def connect(self, name: str) -> ProviderConnection:
        """Open one exact managed writer and attach its actual lifetime to the shared lease."""
        with self._mutex:
            if not self._accepting or name not in DATABASES:
                raise ValueError("provider storage is closed or the database is not managed")
            if name in self._active:
                raise BlockingIOError("provider database already has its actual writer")
            self._verify()
            connection = sqlite3.connect(
                self.root / name, check_same_thread=False, factory=ProviderConnection
            )
            try:
                self._verify()
                self._active.add(name)
                connection._provider_close_lock = self._mutex
                connection._provider_release = lambda: self._release(name)
                return connection
            except BaseException:
                connection.close()
                raise

    def close_unopened(self) -> None:
        """Release failed initialization only when no actual SQLite writer remains."""
        with self._mutex:
            if not self._active:
                self._unlock()
