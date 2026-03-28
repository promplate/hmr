import sys
from argparse import SUPPRESS, ArgumentParser
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import find_spec, module_from_spec
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App

__version__ = "0.0.1"

__all__ = ["cli", "run_with_hmr"]

LOADER_MODULE_NAME = "textual_hmr_app"


@dataclass(frozen=True, slots=True)
class _Target:
    module_or_path: str
    attr: str
    entry_file: Path
    is_path: bool

    @classmethod
    def parse(cls, target: str):
        if ":" not in target[1:-1]:
            raise ValueError(f"The target must be in the format 'module:attr' (e.g. 'main:DemoApp') or 'path:attr' (e.g. './main.py:DemoApp'). Got: '{target}'")
        module_or_path, attr = target.rsplit(":", 1)
        file = Path(module_or_path)
        if file.is_file():
            return cls(module_or_path, attr, file.resolve(), is_path=True)
        if "." in module_or_path:
            from reactivity.hmr.core import patch_meta_path

            patch_meta_path()

        if (spec := find_spec(module_or_path)) is None or spec.origin is None:
            raise ModuleNotFoundError(module_or_path)

        return cls(module_or_path, attr, Path(spec.origin).resolve(), is_path=False)

    def prepare_import_paths(self):
        cwd = str(Path.cwd())
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        if self.is_path:
            parent = str(self.entry_file.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)

    def load(self):
        if self.is_path:
            if (module := sys.modules.get(LOADER_MODULE_NAME)) is None:
                # Reuse a stable module name so HMR can update the same module object across reloads.
                from reactivity.hmr.core import _loader

                sys.modules[LOADER_MODULE_NAME] = module = module_from_spec(ModuleSpec(LOADER_MODULE_NAME, _loader, origin=self.module_or_path))
            return getattr(module, self.attr)
        return getattr(import_module(self.module_or_path), self.attr)


def _coerce_app(target: object, *, watch_css: bool):
    from textual.app import App

    if isinstance(target, App):
        return target
    if isinstance(target, type) and issubclass(target, App):
        return target(watch_css=watch_css)
    if isinstance(target, Callable):
        app = target()
        if isinstance(app, App):
            return app
    raise TypeError("The target must be a Textual App subclass, an App instance, or a zero-argument callable returning an App.")


async def run_with_hmr(
    target: str,
    *,
    watch: list[str] | None = None,
    ignore: list[str] | None = None,
    headless: bool = False,
    inline: bool = False,
    inline_no_clear: bool = False,
    mouse: bool = True,
    clear: bool = False,
    watch_css: bool = True,
):
    from asyncio import Event, create_task

    from reactivity import async_derived
    from reactivity.hmr.core import HMR_CONTEXT, AsyncReloader, ReactiveModule
    from reactivity.hmr.fs import fs_signals
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    resolved_target = _Target.parse(target)
    watch = [str(resolved_target.entry_file.parent), *(watch or [])]
    ignore = ignore or []
    resolved_target.prepare_import_paths()

    need_restart = True
    current_app: App | None = None
    main_loop_started = Event()
    reloading = Event()

    class Reloader(AsyncReloader):
        def __init__(self):
            super().__init__(str(resolved_target.entry_file), [str(resolved_target.entry_file), *watch], ignore)
            self.error_filter.exclude_filenames.add(__file__)
            # Keep app construction and early startup inside the reactive load so file access during startup is tracked too.
            self._load = async_derived(self.__load, context=HMR_CONTEXT)

        async def __load(self):
            app = _coerce_app(resolved_target.load(), watch_css=watch_css)
            started = Event()

            def mark_running():
                main_loop_started.set()
                reloading.clear()
                started.set()

            async def mark_app_ready(_pilot):
                # Textual calls the auto-pilot callback after `_ready()`, which is the public hook closest to "app is running".
                mark_running()

            async def run_app():
                try:
                    return await app.run_async(headless=headless, inline=inline, inline_no_clear=inline_no_clear, mouse=mouse, auto_pilot=mark_app_ready)
                finally:
                    started.set()

            task = create_task(run_app())
            await started.wait()
            return app, task

        async def load_app(self):
            while True:
                app, task = await self._load()
                if not self._load.dirty:
                    return app, task
                app.exit()
                await task

        async def __aenter__(self):
            call_pre_reload_hooks()
            call_post_reload_hooks()
            self._watch_task = create_task(self.start_watching())
            return self

        async def __aexit__(self, *_):
            self.stop_watching()
            await self._watch_task

        async def start_watching(self):
            await main_loop_started.wait()
            return await super().start_watching()

        def on_changes(self, files: set[Path]):
            if watch_css:
                # Let Textual's own CSS watcher handle stylesheet edits without restarting the whole app.
                files = {path for path in files if path.suffix.lower() not in {".css", ".tcss"}}
                if not files:
                    return None
            if not (files.intersection(ReactiveModule.instances) or files.intersection(path for path, signal in fs_signals.items() if signal.subscribers)):
                return None
            changed = super().on_changes(files | {resolved_target.entry_file})
            if reloading.is_set():
                return changed
            nonlocal need_restart
            if clear:
                print("\033c", end="", flush=True)
            need_restart = True
            reloading.set()
            if current_app is not None and current_app.is_running:
                current_app.exit()
            return changed

    last_return = None
    last_return_code = 0

    async with Reloader() as reloader:
        while need_restart:
            need_restart = False
            with reloader.error_filter:
                reloading.set()
                main_loop_started.clear()
                current_app, current_task = await reloader.load_app()
                try:
                    last_return = await current_task
                    assert current_app is not None
                    last_return_code = current_app.return_code or 0
                finally:
                    main_loop_started.clear()
                    current_app = None

    return last_return, last_return_code


def cli(argv: list[str] = sys.argv[1:]):
    parser = ArgumentParser("textual-hmr", description="Hot Reloading for Textual apps")
    if sys.version_info >= (3, 14):
        parser.suggest_on_error = True
    parser.add_argument("target", help="The import path of the Textual app. Supports module:attr and path:attr.")
    parser.add_argument("--watch", action="append", default=[], help="Additional file or directory paths to watch.")
    parser.add_argument("--ignore", action="append", default=[], help="File or directory paths to ignore.")
    parser.add_argument("--headless", action="store_true", help="Run without terminal output.")
    parser.add_argument("--inline", action="store_true", help="Run the app inline under the prompt.")
    parser.add_argument("--inline-no-clear", action="store_true", help="Preserve inline output after exit.")
    parser.add_argument("--no-mouse", action="store_true", help="Disable mouse support.")
    parser.add_argument("--clear", action="store_true", help="Clear the terminal before restarting after changes.")
    parser.add_argument("--no-watch-css", action="store_true", help="Do not enable Textual's CSS watcher for App subclasses.")
    parser.add_argument("--version", action="version", version=f"textual-hmr {__version__}", help=SUPPRESS)
    args = parser.parse_args(argv)

    from asyncio import run

    _, return_code = run(
        run_with_hmr(
            args.target,
            watch=args.watch,
            ignore=args.ignore,
            headless=args.headless,
            inline=args.inline,
            inline_no_clear=args.inline_no_clear,
            mouse=not args.no_mouse,
            clear=args.clear,
            watch_css=not args.no_watch_css,
        )
    )
    raise SystemExit(return_code)


if __name__ == "__main__":
    cli()
