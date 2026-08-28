from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

DEBOUNCE_SECONDS = 0.5


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: VaultWatcher) -> None:
        self._watcher = watcher

    def _handle(self, event: FileSystemEvent, change: str) -> None:
        if event.is_directory:
            return
        src_path = str(event.src_path)
        if not src_path.endswith(".md"):
            return
        self._watcher.schedule(Path(src_path), change)

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event, "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event, "modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event, "deleted")


class VaultWatcher:
    def __init__(
        self,
        vault_root: Path,
        on_change: Callable[[Path, str], None],
        debounce_seconds: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._vault_root = Path(vault_root)
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._observer: Observer | None = None
        self._pending: dict[Path, str] = {}
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._observer is not None:
            return
        observer = Observer()
        observer.daemon = True
        observer.schedule(_VaultEventHandler(self), str(self._vault_root), recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()

    def schedule(self, path: Path, change: str) -> None:
        with self._lock:
            # Obsidian writes a file several times per save; the last change
            # for a path within the debounce window is the one that counts.
            self._pending[path] = change
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None

        for path, change in pending.items():
            self._on_change(path, change)
