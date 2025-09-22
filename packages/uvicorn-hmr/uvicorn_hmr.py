import sys
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, override

from typer import Argument, Option, Typer, secho

app = Typer(help="Hot Module Replacement for Uvicorn", add_completion=False, pretty_exceptions_show_locals=False)


@app.command(no_args_is_help=True)
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
    if reload:
        secho("\nWarning: The `--reload` flag is deprecated in favor of `--refresh` to avoid ambiguity.\n", fg="yellow")
        refresh = reload  # For backward compatibility, map reload to refresh
    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        exit(1)
    module, attr = slug.split(":")

    fragment = module.replace(".", "/")

    for path in ("", *sys.path):
        if (file := Path(path, f"{fragment}.py")).is_file():
            break
        if (file := Path(path, fragment, "__init__.py")).is_file():
            break
    else:
        secho("Module", fg="red", nl=False)
        secho(f" {module} ", fg="yellow", nl=False)
        secho("not found.", fg="red")
        exit(1)

    file = file.resolve()

    if module in sys.modules:
        return secho(
            f"It seems you've already imported `{module}` as a normal module. You should call `reactivity.hmr.core.patch_meta_path()` before it.",
            fg="red",
        )

    from atexit import register
    from importlib import import_module
    from logging import getLogger
    from threading import Event, Thread

    from reactivity.hmr.core import ReactiveModule, SyncReloader, is_relative_to_any
    from reactivity.hmr.utils import on_dispose
    from uvicorn import Config, Server
    from watchfiles import Change

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    class Reloader(SyncReloader):
        def __init__(self):
            super().__init__(str(file), [str(file), *reload_include], reload_exclude)
            self.error_filter.exclude_filenames.add(__file__)  # exclude error stacks within this file
            register(lambda: self.stop_server())

        def stop_server(self):
            pass

        @override
        def run_entry_file(self):
            self.stop_server()
            with self.error_filter:
                app = getattr(import_module(module), attr)
                if refresh:
                    app = _try_patch(app)

                server = Server(Config(app, host, port, env_file=env_file, log_level=log_level))

                finish = Event()

                def run_server():
                    watched_paths = [Path(p).resolve() for p in self.includes]
                    ignored_paths = [Path(p).resolve() for p in self.excludes]
                    if all(is_relative_to_any(path, ignored_paths) or not is_relative_to_any(path, watched_paths) for path in ReactiveModule.instances):
                        logger.error("No files to watch for changes. The server will never reload.")
                    server.run()
                    finish.set()

                Thread(target=run_server, daemon=True).start()

                def stop_server():
                    logger.warning("Application '%s' has changed. Restarting server...", slug)
                    if refresh:
                        _try_reload()
                    server.should_exit = True
                    try:
                        finish.wait()
                    except KeyboardInterrupt:
                        server.force_exit = True

                self.stop_server = stop_server

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

                if clear:
                    print("\033c", end="", flush=True)
                logger.warning("Watchfiles detected changes in %s. Reloading...", ", ".join(map(_display_path, paths)))
            return super().on_events(events)

    __load = ReactiveModule.__load if TYPE_CHECKING else ReactiveModule._ReactiveModule__load  # noqa: SLF001

    @wraps(original_load := __load.method)
    def patched_load(self: ReactiveModule, *args, **kwargs):
        original_load(self, *args, **kwargs)
        file: Path = self._ReactiveModule__file  # type: ignore
        on_dispose(lambda: logger.info("Reloading module '%s' from %s", self.__name__, _display_path(file)), str(file))

    __load.method = patched_load

    logger = getLogger("uvicorn.error")
    Reloader().keep_watching_until_interrupt()

    __load.method = original_load


def _display_path(path: str | Path):
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


def _try_patch(app):
    try:
        from fastapi_reloader import patch_for_auto_reloading

        return patch_for_auto_reloading(app)

    except ImportError:
        secho(NOTE, fg="red")
        raise


def _try_reload():
    try:
        from fastapi_reloader import send_reload_signal

        send_reload_signal()
    except ImportError:
        secho(NOTE, fg="red")
        raise


if __name__ == "__main__":
    app()
