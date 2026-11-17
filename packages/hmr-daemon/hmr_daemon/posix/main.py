from collections.abc import Iterable
from functools import wraps
from os import getenv
from pathlib import Path
from subprocess import Popen, TimeoutExpired
from threading import Event, Thread, local

from reactivity.hmr import __file__ as hmr_file
from reactivity.hmr.core import BaseReloader, ErrorFilter, ReactiveModuleLoader, SyncReloader, patch_meta_path
from watchfiles import Change


def get_code(_: ReactiveModuleLoader, fullname: str):
    from ast import parse
    from importlib.util import find_spec

    if (spec := find_spec(fullname)) is not None and (file := spec.origin) is not None:
        path = Path(file)
        return compile(parse(path.read_text(), str(path)), str(path), "exec", dont_inherit=True)


ReactiveModuleLoader.get_code = get_code  # type: ignore

# Shared shutdown event (pipe reader thread stops when set)
shutdown_event = Event()

# Compute excludes (e.g. virtualenv) similar to windows implementation
excludes = (venv,) if (venv := getenv("VIRTUAL_ENV")) else ()

# Patch import machinery early
patch_meta_path(excludes=excludes)

# State for first patch decision
(state := local()).disabled = False


# Preserve original __init__ so we can disable after first construction
def patch(process: Popen):
    global original_init

    @wraps(original_init := BaseReloader.__init__)
    def wrapper(*args, **kwargs):
        if not state.disabled:
            shutdown_event.set()
            BaseReloader.__init__ = original_init
            process.terminate()
        original_init(*args, **kwargs)

    BaseReloader.__init__ = wrapper


class PipeReloader(SyncReloader):
    def __init__(self, process: Popen):
        self._process = process
        self.includes = (".",)
        self.excludes = excludes
        self.error_filter = ErrorFilter(*map(str, Path(hmr_file, "..").resolve().glob("**/*.py")), __file__)

    def iterate_pipe_events(self) -> Iterable[set[tuple[Change, str]]]:
        from json import loads

        from watchfiles import Change

        while not shutdown_event.is_set():
            # Check if worker process is still alive
            if self._process.poll() is not None:
                # Worker process terminated
                return

            # Read data from pipe
            try:
                assert self._process.stdout is not None
                line = self._process.stdout.readline()
                if not line:  # EOF
                    return
            except OSError:
                # Pipe error
                return

            # Process events - each line is a complete events list
            if events_data := loads(line.decode()):
                yield {(Change(event_int), path) for event_int, path in events_data}

    def start_watching(self):
        for events in self.iterate_pipe_events():
            if shutdown_event.is_set():
                return
            self.on_events(events)

    def cleanup(self):
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=0.1)
            except TimeoutExpired:
                self._process.kill()


def _watch(process: Popen):
    if shutdown_event.is_set():
        return
    reloader = PipeReloader(process)
    try:
        reloader.start_watching()
    finally:
        reloader.cleanup()


def run_reloader(process: Popen):
    def watch():
        state.disabled = True
        try:
            _watch(process)
        finally:
            shutdown_event.set()

    patch(process)
    Thread(target=watch, daemon=True, name="hmr-daemon").start()
