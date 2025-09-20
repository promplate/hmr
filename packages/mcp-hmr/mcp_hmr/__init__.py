import asyncio
import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, override

from typer import Argument, Option, Typer, secho

app = Typer(help="Hot Module Replacement for MCP (Model Context Protocol) servers", add_completion=False, pretty_exceptions_show_locals=False)


@app.command(no_args_is_help=True)
def main(
    slug: Annotated[str, Argument(help="MCP server module and function (e.g., 'server:main' or 'server:app')")] = "server:main",
    reload_include: list[str] = [str(Path.cwd())],  # noqa: B006, B008
    reload_exclude: list[str] = [".venv", "__pycache__", "*.pyc"],  # noqa: B006
    transport: Annotated[str, Option("--transport", help="Transport method: 'stdio' or 'sse'")] = "stdio",  # noqa: ARG001
    clear: Annotated[bool, Option("--clear", help="Clear the terminal before restarting the server")] = False,  # noqa: FBT002
):
    """
    Start an MCP server with hot module reloading.

    The slug should point to your MCP server function, e.g.:
    - server:main (calls main() function in server.py)
    - mypackage.server:run_server (calls run_server() in mypackage/server.py)
    """
    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        secho(" (should be in format 'module:function')", fg="red")
        exit(1)

    module, attr = slug.split(":", 1)

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
        secho("Module ", fg="red", nl=False)
        secho(f"{module} ", fg="yellow", nl=False)
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
    from watchfiles import Change

    if TYPE_CHECKING:
        from collections.abc import Callable

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    @register
    def _():
        stop_server()

    def stop_server():
        pass

    def start_server(server_func: "Callable"):
        nonlocal stop_server

        server_task = None
        server_process = None
        finish = Event()

        def run_server():
            nonlocal server_task, server_process
            watched_paths = [Path(p).resolve() for p in (file, *reload_include)]
            ignored_paths = [Path(p).resolve() for p in reloader.excludes]
            if all(is_relative_to_any(path, ignored_paths) or not is_relative_to_any(path, watched_paths) for path in ReactiveModule.instances):
                logger.error("No files to watch for changes. The server will never reload.")

            try:
                if asyncio.iscoroutinefunction(server_func):
                    # If it's an async function, run it in an event loop
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    server_task = loop.create_task(server_func())
                    try:
                        loop.run_until_complete(server_task)
                    except asyncio.CancelledError:
                        pass
                    finally:
                        loop.close()
                else:
                    # If it's a sync function, just call it
                    server_func()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                logger.error(f"Server error: {e}")
            finally:
                finish.set()

        server_thread = Thread(target=run_server, daemon=True)
        server_thread.start()

        def stop_server():
            nonlocal server_task
            if server_task and not server_task.done():
                server_task.cancel()
            finish.set()

    class Reloader(SyncReloader):
        def __init__(self):
            super().__init__(str(file), reload_include, reload_exclude)
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
                server_func = getattr(self.entry_module, attr)
                start_server(server_func)

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
                logger.warning("MCP server '%s' has changed. Restarting server...", slug)

            def log_module_reload(self: ReactiveModule):
                ns = self.__dict__
                logger.info("Reloading module '%s' from %s", ns["__name__"], _display_path(ns["__file__"]))

            with (
                when(ReactiveModule._ReactiveModule__load.method, "<start>").do(log_module_reload),  # type: ignore  # noqa: SLF001
                when(self.run_entry_file, "<start>").do(log_server_restart),
            ):
                return super().start_watching()

    logger = getLogger("mcp.hmr")
    logger.setLevel("INFO")

    # Set up console handler if not already present
    if not logger.handlers:
        import logging

        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    try:
        (reloader := Reloader()).keep_watching_until_interrupt()
    except KeyboardInterrupt:
        logger.info("Shutting down MCP server...")
    finally:
        stop_server()


def _display_path(path: str | Path):
    p = Path(path).resolve()
    try:
        return f"'{p.relative_to(Path.cwd())}'"
    except ValueError:
        return f"'{p}'"


if __name__ == "__main__":
    app()
