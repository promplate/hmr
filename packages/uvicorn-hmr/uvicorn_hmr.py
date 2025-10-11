import sys
from collections.abc import Callable
from pathlib import Path
from time import time
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

    from asyncio import FIRST_COMPLETED, Event, Future, ensure_future, run, sleep, wait
    from functools import cache, wraps
    from importlib import import_module
    from logging import getLogger
    from signal import SIGINT

    from reactivity import state
    from reactivity.hmr.core import HMR_CONTEXT, AsyncReloader, ReactiveModule, is_relative_to_any
    from reactivity.hmr.fs import fs_signals
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks
    from reactivity.hmr.utils import on_dispose

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    class Reloader(AsyncReloader):
        def __init__(self):
            super().__init__(str(file), [str(file), *reload_include], reload_exclude)
            self.error_filter.exclude_filenames.add(__file__)  # exclude error stacks within this file
            self.ready = Event()
            self._run = HMR_CONTEXT.async_derived(self.__run)

        async def __run(self):
            if server:
                logger.warning("Application '%s' has changed. Restarting server...", slug)
                self.ready.clear()
                await main_loop_started.wait()
                server.should_exit = True
                await finish.wait()
            with self.error_filter:
                self.app = getattr(import_module(module), attr)
                if refresh:
                    self.app = _try_patch(self.app)
                watched_paths = [Path(p).resolve() for p in self.includes]
                ignored_paths = [Path(p).resolve() for p in self.excludes]
                if all(is_relative_to_any(path, ignored_paths) or not is_relative_to_any(path, watched_paths) for path in ReactiveModule.instances):
                    logger.error("No files to watch for changes. The server will never reload.")
            return self.app

        async def run(self):
            while True:
                await self._run()
                if not self._run.dirty:  # in case user code changed during reload
                    break
            self.ready.set()

        async def __aenter__(self):
            call_pre_reload_hooks()
            self.__run_effect = HMR_CONTEXT.async_effect(self.run, call_immediately=False)
            await self.__run_effect()
            call_post_reload_hooks()
            self.__reloader_task = ensure_future(self.start_watching())
            return self

        async def __aexit__(self, *_):
            self.stop_watching()
            self.__run_effect.dispose()
            await self.__reloader_task

        async def start_watching(self):
            await main_loop_started.wait()
            return await super().start_watching()

        @override
        def on_changes(self, files: set[Path]):
            if files.intersection(ReactiveModule.instances) or files.intersection(path for path, s in fs_signals.items() if s.subscribers):
                if clear:
                    print("\033c", end="", flush=True)
                logger.warning("Watchfiles detected changes in %s. Reloading...", ", ".join(map(_display_path, files)))
                nonlocal need_restart
                need_restart = True
                return super().on_changes(files)

    main_loop_started = Event()

    def until(func: Callable[[], bool]):
        future = Future()

        @HMR_CONTEXT.effect
        def check():
            if func():
                future.set_result(None)
                check.dispose()

        return future

    @cache
    def lazy_import_from_uvicorn():
        from uvicorn import Config, Server

        class _Server(Server):
            should_exit = state(False, context=HMR_CONTEXT)  # noqa: FBT003

            def handle_exit(self, sig, frame):
                if self.force_exit and sig == SIGINT:
                    raise KeyboardInterrupt  # allow immediate shutdown on third interrupt
                return super().handle_exit(sig, frame)

            async def main_loop(self):
                main_loop_started.set()
                if self.on_tick(0):
                    return

                async def ticking():
                    counter = 10
                    while not self.should_exit:
                        await sleep(1 - time() % 1)
                        self.should_exit = await self.on_tick(counter)
                        counter += 10

                await wait((until(lambda: self.should_exit), ensure_future(ticking())), return_when=FIRST_COMPLETED)

            if refresh:

                def shutdown(self, sockets=None):
                    _try_refresh()
                    return super().shutdown(sockets)

        return _Server, Config

    __load = ReactiveModule.__load if TYPE_CHECKING else ReactiveModule._ReactiveModule__load  # noqa: SLF001

    @wraps(original_load := __load.method)
    def patched_load(self: ReactiveModule, *args, **kwargs):
        original_load(self, *args, **kwargs)
        file: Path = self._ReactiveModule__file  # type: ignore
        on_dispose(lambda: logger.info("Reloading module '%s' from %s", self.__name__, _display_path(file)), str(file))

    __load.method = patched_load

    logger = getLogger("uvicorn.error")

    need_restart = True
    server = None
    finish = Event()

    async def main():
        nonlocal need_restart, server

        async with Reloader() as reloader:
            while need_restart:
                need_restart = False
                with reloader.error_filter:
                    await reloader.ready.wait()
                    _Server, Config = lazy_import_from_uvicorn()  # noqa: N806
                    server = _Server(Config(reloader.app, host, port, env_file=env_file, log_level=log_level))
                    try:
                        await server.serve()
                        main_loop_started.clear()
                    except KeyboardInterrupt:
                        break
                    finally:
                        finish.set()
                        finish.clear()
                        server = None

    run(main())

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


def _try_refresh():
    try:
        from fastapi_reloader import send_reload_signal

        send_reload_signal()
    except ImportError:
        secho(NOTE, fg="red")
        raise


if __name__ == "__main__":
    app()
