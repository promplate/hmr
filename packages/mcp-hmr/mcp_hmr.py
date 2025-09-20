from __future__ import annotations

import asyncio
import inspect
import sys
from atexit import register
from functools import cached_property
from importlib.machinery import ModuleSpec
from logging import getLogger
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, Annotated, Awaitable, Callable, override

from typer import Argument, Option, Typer, secho


app = Typer(help="Hot Module Reloading for MCP-like async servers", add_completion=False, pretty_exceptions_show_locals=False)


@app.command(no_args_is_help=True)
def main(
    slug: Annotated[str, Argument()] = "server:serve",
    reload_include: list[str] = [str(Path.cwd())],  # noqa: B006, B008
    reload_exclude: list[str] = [".venv"],  # noqa: B006
    clear: Annotated[bool, Option("--clear", help="Clear the terminal before restarting the server")] = False,  # noqa: FBT002
):
    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        raise SystemExit(1)

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
        raise SystemExit(1)

    file = file.resolve()

    if module in sys.modules:
        return secho(
            f"It seems you've already imported `{module}` as a normal module. You should call `reactivity.hmr.core.patch_meta_path()` before it.",
            fg="red",
        )

    from reactivity.hmr.core import ReactiveModule, ReactiveModuleLoader, SyncReloader, __version__, is_relative_to_any
    from reactivity.hmr.utils import load
    from watchfiles import Change

    if TYPE_CHECKING:
        ServerCallable = Callable[[], Awaitable[object] | object]

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    # Async server loop management
    loop = asyncio.new_event_loop()
    asyncio_thread_finished = Event()
    current_task: asyncio.Task | None = None

    def _run_loop():
        try:
            asyncio.set_event_loop(loop)
            loop.run_forever()
        finally:
            asyncio_thread_finished.set()

    Thread(target=_run_loop, daemon=True, name="mcp-hmr-loop").start()

    @register
    def _stop_all():
        stop_server()
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        try:
            asyncio_thread_finished.wait(timeout=0.5)
        except KeyboardInterrupt:
            pass

    def stop_server():
        nonlocal current_task
        if current_task is None:
            return
        task = current_task
        current_task = None

        async def _cancel():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_cancel(), loop)
            fut.result(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

    def start_server(fn: "ServerCallable"):
        nonlocal current_task

        def _start() -> asyncio.Future:
            try:
                result = fn()
                if inspect.isawaitable(result):
                    awaitable_obj = result  # type: ignore[assignment]
                elif callable(result):
                    maybe = result()  # type: ignore[call-arg]
                    if inspect.isawaitable(maybe):
                        awaitable_obj = maybe  # type: ignore[assignment]
                    else:
                        raise TypeError("Entry callable must be or return an awaitable")
                else:
                    raise TypeError("Entry callable must be or return an awaitable")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to start server: %s", exc)
                raise
            # Accept any awaitable (not just coroutine) using ensure_future
            return asyncio.ensure_future(awaitable_obj, loop=loop)

        try:
            from threading import Event as ThreadEvent

            ready = ThreadEvent()

            # Using call_soon_threadsafe to create task on loop thread
            def _cb():
                nonlocal current_task
                current_task = _start()
                ready.set()

            loop.call_soon_threadsafe(_cb)
            if not ready.wait(timeout=1.0):
                raise TimeoutError("Timeout scheduling server task")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to schedule server: %s", exc)

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
                server_callable = getattr(self.entry_module, attr)
                if not callable(server_callable):
                    raise TypeError(f"Attribute '{attr}' on module '{module}' is not callable")
                start_server(server_callable)

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

    logger = getLogger("mcp-hmr")
    (reloader := Reloader()).keep_watching_until_interrupt()
    stop_server()


def _display_path(path: str | Path):
    p = Path(path).resolve()
    try:
        return f"'{p.relative_to(Path.cwd())}'"
    except ValueError:
        return f"'{p}'"


if __name__ == "__main__":
    app()

