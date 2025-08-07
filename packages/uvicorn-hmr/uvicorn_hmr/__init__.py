"""Hot Module Replacement for Uvicorn."""

import sys
from atexit import register
from functools import cached_property
from importlib.machinery import ModuleSpec
from logging import getLogger
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, Annotated, override

from reactivity.hmr.core import ReactiveModule, ReactiveModuleLoader, SyncReloader, __version__, is_relative_to_any
from reactivity.hmr.utils import load
from typer import Argument, Option, Typer, secho
from uvicorn import Config, Server
from watchfiles import Change

if TYPE_CHECKING:
    from uvicorn._types import ASGIApplication


def display_path(path: str | Path):
    """Format a file path for display, showing relative path if possible."""
    p = Path(path).resolve()
    try:
        return f"'{p.relative_to(Path.cwd())}'"
    except ValueError:
        return f"'{p}'"


NOTE = """
When you enable the `--refresh` flag, it means you want to use the `fastapi-reloader` package to enable automatic HTML page refreshing.
This behavior differs from Uvicorn's built-in `--reload` functionality.

Server reloading is a core feature of `uvicorn-hmr` and is always active, regardless of whether the `--refresh` flag is set.
The `--refresh` flag specifically controls auto-refreshing of HTML pages, a feature not available in Uvicorn.

If you don't need HTML page auto-refreshing, simply omit the `--refresh` flag.
If you do want this feature, ensure that `fastapi-reloader` is installed by running: `pip install fastapi-reloader` or `pip install uvicorn-hmr[all]`.
"""


def try_patch(app):
    """Try to patch the app for auto-reloading using fastapi-reloader."""
    try:
        from fastapi_reloader import patch_for_auto_reloading

        return patch_for_auto_reloading(app)

    except ImportError:
        secho(NOTE, fg="red")
        raise


def try_reload():
    """Try to send reload signal using fastapi-reloader."""
    try:
        from fastapi_reloader import send_reload_signal

        send_reload_signal()
    except ImportError:
        secho(NOTE, fg="red")
        raise


class ServerManager:
    """Manages the Uvicorn server lifecycle."""

    def __init__(self, host: str, port: int, env_file: Path | None, log_level: str | None, refresh: bool):  # noqa: FBT001
        self.host = host
        self.port = port
        self.env_file = env_file
        self.log_level = log_level
        self.refresh = refresh
        self._stop_server_func = lambda: None

    def start_server(self, app: "ASGIApplication", file: Path, reload_include: list[str], reloader):
        """Start the Uvicorn server in a separate thread."""
        server = Server(Config(app, self.host, self.port, env_file=self.env_file, log_level=self.log_level))
        finish = Event()

        def run_server():
            watched_paths = [Path(p).resolve() for p in (file, *reload_include)]
            ignored_paths = [Path(p).resolve() for p in reloader.excludes]
            if all(is_relative_to_any(path, ignored_paths) or not is_relative_to_any(path, watched_paths) for path in ReactiveModule.instances):
                from logging import getLogger

                logger = getLogger("uvicorn.error")
                logger.error("No files to watch for changes. The server will never reload.")
            server.run()
            finish.set()

        Thread(target=run_server, daemon=True).start()

        def stop_server():
            if self.refresh:
                try_reload()
            server.should_exit = True
            finish.wait()

        self._stop_server_func = stop_server
        return stop_server

    def stop_server(self):
        """Stop the current server."""
        self._stop_server_func()


class UvicornReloader(SyncReloader):
    """Custom reloader for uvicorn-hmr that extends SyncReloader."""

    def __init__(self, file: Path, module: str, attr: str, reload_include: list[str], reload_exclude: list[str], is_package: bool, refresh: bool, clear: bool, slug: str, server_manager):  # noqa: FBT001
        super().__init__(str(file), reload_include, reload_exclude)
        self.file = file
        self.module = module
        self.attr = attr
        self.is_package = is_package
        self.refresh = refresh
        self.clear = clear
        self.slug = slug
        self.server_manager = server_manager
        self.logger = getLogger("uvicorn.error")
        self.error_filter.exclude_filenames.add(__file__)  # exclude error stacks within this file

    @cached_property
    @override
    def entry_module(self):
        if "." in self.module:
            __import__(self.module.rsplit(".", 1)[0])  # ensure parent modules are imported

        if __version__ >= "0.6.4":
            from reactivity.hmr.core import _loader as loader
        else:
            loader = ReactiveModuleLoader(self.file)  # type: ignore

        spec = ModuleSpec(self.module, loader, origin=str(self.file), is_package=self.is_package)
        sys.modules[self.module] = mod = loader.create_module(spec)
        loader.exec_module(mod)
        return mod

    @override
    def run_entry_file(self):
        self.server_manager.stop_server()
        with self.error_filter:
            load(self.entry_module)
            app = getattr(self.entry_module, self.attr)
            if self.refresh:
                app = try_patch(app)
            self.server_manager.start_server(app, self.file, self.includes, self)

    @override
    def on_events(self, events):
        if events:
            paths: list[Path] = []
            for type, file in events:
                path = Path(file).resolve()
                if type != Change.deleted and path in ReactiveModule.instances:
                    paths.append(path)
            if not paths:
                return

            if self.clear:
                print("\033c", end="")
            self.logger.warning("Watchfiles detected changes in %s. Reloading...", ", ".join(map(display_path, paths)))
        return super().on_events(events)

    @override
    def start_watching(self):
        from dowhen import when

        def log_server_restart():
            self.logger.warning("Application '%s' has changed. Restarting server...", self.slug)

        def log_module_reload(module: ReactiveModule):
            ns = module.__dict__
            self.logger.info("Reloading module '%s' from %s", ns["__name__"], display_path(ns["__file__"]))

        with (
            when(ReactiveModule._ReactiveModule__load.method, "<start>").do(log_module_reload),  # type: ignore  # noqa: SLF001
            when(self.run_entry_file, "<start>").do(log_server_restart),
        ):
            return super().start_watching()


def main(
    slug: Annotated[str, Argument()] = "main:app",
    reload_include: list[str] = [str(Path.cwd())],  # noqa: B006, B008
    reload_exclude: list[str] = [".venv"],  # noqa: B006
    host: str = "localhost",
    port: int = 8000,
    env_file: Path | None = None,
    log_level: str | None = "info",
    refresh: Annotated[bool, Option("--refresh", help="Enable automatic browser page refreshing with `fastapi-reloader` (requires installation)")] = False,  # noqa: FBT002
    clear: Annotated[bool, Option("--clear", help="Clear the terminal before restarting the server")] = False,  # noqa: FBT002
    reload: Annotated[bool, Option("--reload", hidden=True)] = False,  # noqa: FBT002
):
    """Hot Module Replacement for Uvicorn."""
    if reload:
        secho("\nWarning: The `--reload` flag is deprecated in favor of `--refresh` to avoid ambiguity.\n", fg="yellow")
        refresh = reload  # For backward compatibility, map reload to refresh

    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        exit(1)

    module, attr = slug.split(":")
    fragment = module.replace(".", "/")

    # Find the module file
    file: Path | None
    is_package = False
    for path in ("", *sys.path):
        if (file := Path(path, f"{fragment}.py")).is_file():
            is_package = False
            break
        if (file := Path(path, fragment, "__init__.py")).is_file():
            is_package = True
            break
    else:
        file = None

    if file is None:
        secho("Module", fg="red", nl=False)
        secho(f" {module} ", fg="yellow", nl=False)
        secho("not found.", fg="red")
        exit(1)

    if module in sys.modules:
        return secho(
            f"It seems you've already imported `{module}` as a normal module. You should call `reactivity.hmr.core.patch_meta_path()` before it.",
            fg="red",
        )

    # Add current directory to sys.path if not already there
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    # Create server manager and reloader
    server_manager = ServerManager(host, port, env_file, log_level, refresh)
    reloader = UvicornReloader(file, module, attr, reload_include, reload_exclude, is_package, refresh, clear, slug, server_manager)

    # Register cleanup function
    @register
    def cleanup():
        server_manager.stop_server()

    # Start watching for changes
    reloader.keep_watching_until_interrupt()
    server_manager.stop_server()


# Create the main Typer app - this preserves the original uvicorn_hmr:app interface
app = Typer(help="Hot Module Replacement for Uvicorn", add_completion=False, pretty_exceptions_show_locals=False)

# Register the main command
app.command(no_args_is_help=True)(main)
