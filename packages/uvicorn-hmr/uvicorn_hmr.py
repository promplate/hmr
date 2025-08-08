import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, override

from typer import Argument, Option, Typer, secho

app = Typer(help="Hot Module Replacement for Uvicorn", add_completion=False, pretty_exceptions_show_locals=False)


@app.command(no_args_is_help=True)
def main(
    slug: Annotated[str, Argument()] = "main:app",
    reload_include: str = str(Path.cwd()),
    reload_exclude: str = ".venv",
    host: str = "localhost",
    port: int = 8000,
    env_file: Path | None = None,
    log_level: str | None = "info",
    reload: Annotated[bool, Option("--reload", help="Enable automatic browser page reload using `fastapi-reloader` (requires installation)")] = False,  # noqa: FBT002
    clear: Annotated[bool, Option("--clear", help="Clear the terminal before restarting the server")] = False,  # noqa: FBT002
):
    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        exit(1)
    module, attr = slug.split(":")

    fragment = module.replace(".", "/")

    is_package = False
    for path in ("", *sys.path):
        if (file := Path(path, f"{fragment}.py")).is_file():
            is_package = False
            break
        if (file := Path(path, fragment, "__init__.py")).is_file():
            is_package = True
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
    from importlib.machinery import ModuleSpec
    from logging import getLogger
    from threading import Event, Thread

    from reactivity.hmr.core import ReactiveModule, ReactiveModuleLoader, SyncReloader, __version__, is_relative_to_any
    from reactivity.hmr.utils import load
    from uvicorn import Config, Server
    from watchfiles import Change

    if TYPE_CHECKING:
        from uvicorn._types import ASGIApplication

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    @register
    def _():
        stop_server()

    def stop_server():
        pass

    def start_server(app: "ASGIApplication"):
        nonlocal stop_server

        server = Server(Config(app, host, port, env_file=env_file, log_level=log_level))
        finish = Event()

        def run_server():
            path_to_watch = Path(reload_include).resolve()
            ignored_paths = [Path(p).resolve() for p in reloader.excludes]
            if all(is_relative_to_any(path, ignored_paths) or not path.is_relative_to(path_to_watch) for path in ReactiveModule.instances):
                logger.error("No files to watch for changes. The server will never reload.")
            server.run()
            finish.set()

        Thread(target=run_server, daemon=True).start()

        def stop_server():
            if reload:
                _try_reload()
            server.should_exit = True
            finish.wait()

    class Reloader(SyncReloader):
        def __init__(self):
            super().__init__(str(file), {reload_include}, {reload_exclude})
            self.error_filter.exclude_filenames.add(__file__)  # exclude error stacks within this file

        @cached_property
        @override
        def entry_module(self):
            if "." in module:
                __import__(module.rsplit(".", 1)[0])  # ensure parent modules are imported

            if __version__ >= "0.6.4":
                from reactivity.hmr.core import _loader as loader
            else:
                loader = ReactiveModuleLoader(file)  # type: ignore

            spec = ModuleSpec(module, loader, origin=str(file), is_package=is_package)
            sys.modules[module] = mod = loader.create_module(spec)
            loader.exec_module(mod)
            return mod

        @override
        def run_entry_file(self):
            stop_server()
            with self.error_filter:
                load(self.entry_module)
                app = getattr(self.entry_module, attr)
                if reload:
                    app: ASGIApplication = _try_patch(app)  # type: ignore
                start_server(app)

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
                    print("\033c", end="")
                logger.warning("Watchfiles detected changes in %s. Reloading...", ", ".join(map(_display_path, paths)))
            return super().on_events(events)

        @override
        def start_watching(self):
            from dowhen import when

            def log_server_restart():
                logger.warning("Application '%s' has changed. Restarting server...", slug)

            def log_module_reload(self: ReactiveModule):
                ns = self.__dict__
                logger.info("Reloading module '%s' from %s", ns["__name__"], _display_path(ns["__file__"]))

            with (
                when(ReactiveModule._ReactiveModule__load.method, "<start>").do(log_module_reload),  # type: ignore  # noqa: SLF001
                when(self.run_entry_file, "<start>").do(log_server_restart),
            ):
                return super().start_watching()

    logger = getLogger("uvicorn.error")
    (reloader := Reloader()).keep_watching_until_interrupt()
    stop_server()


def _display_path(path: str | Path):
    p = Path(path).resolve()
    try:
        return f"'{p.relative_to(Path.cwd())}'"
    except ValueError:
        return f"'{p}'"


NOTE = """
When you enable the `--reload` flag, it means you want to use the `fastapi-reloader` package to enable automatic HTML page reloading.
This behavior differs from Uvicorn's built-in `--reload` functionality.

Server reloading is a core feature of `uvicorn-hmr` and is always active, regardless of whether the `--reload` flag is set.
The `--reload` flag specifically controls auto-reloading of HTML pages, a feature not available in Uvicorn.

If you don't need HTML page auto-reloading, simply omit the `--reload` flag.
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
