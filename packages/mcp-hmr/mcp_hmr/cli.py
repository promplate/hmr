import sys
from functools import cached_property
from pathlib import Path
from typing import Annotated, override

from typer import Argument, Option, Typer, secho

app = Typer(help="Hot Module Replacement for MCP servers", add_completion=False, pretty_exceptions_show_locals=False)


@app.command(no_args_is_help=True)
def main(
    slug: Annotated[str, Argument()] = "main:create_server",
    reload_include: list[str] = [str(Path.cwd())],  # noqa: B006, B008
    reload_exclude: list[str] = [".venv"],  # noqa: B006
    log_level: str | None = "info",  # noqa: ARG001
    clear: Annotated[bool, Option("--clear", help="Clear the terminal before reloading")] = False,  # noqa: FBT002
):
    """Start an MCP server with hot module reloading."""
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
    from threading import Thread

    from reactivity.hmr.core import ReactiveModule, ReactiveModuleLoader, SyncReloader, __version__, is_relative_to_any
    from reactivity.hmr.utils import load
    from watchfiles import Change


    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    logger = getLogger("mcp-hmr")

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(Path.cwd()))
        except ValueError:
            return str(path)

    @register
    def _():
        stop_server()

    def stop_server():
        pass

    def start_server(server_func):
        nonlocal stop_server

        def run_server():
            watched_paths = [Path(p).resolve() for p in (file, *reload_include)]
            ignored_paths = [Path(p).resolve() for p in reloader.excludes]
            if all(is_relative_to_any(path, ignored_paths) or not is_relative_to_any(path, watched_paths) for path in ReactiveModule.instances):
                logger.error("No files to watch for changes. The server will never reload.")
            # For MCP servers, we don't need to start in a separate thread
            # as the server function handles its own lifecycle
            server_func()

        Thread(target=run_server, daemon=True).start()

        def stop_server():
            # For MCP servers, graceful shutdown might be different
            # This will be handled by the server implementation
            pass

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
                logger.warning("MCP server '%s' has changed. Reloading...", slug)

            def log_module_reload(self: ReactiveModule):
                ns = self.__dict__
                logger.info("Reloading module '%s' from %s", ns["__name__"], _display_path(ns["__file__"]))

            with (
                when(ReactiveModule._ReactiveModule__load.method, "<start>").do(log_module_reload),  # type: ignore  # noqa: SLF001
                when(self.run_entry_file, "<start>").do(log_server_restart),
            ):
                super().start_watching()

    reloader = Reloader()
    reloader.start_watching()


if __name__ == "__main__":
    app()
