import sys
from functools import cached_property
from importlib.machinery import ModuleSpec
from logging import getLogger
from pathlib import Path
from typing import override

from reactivity.hmr.core import ReactiveModule, ReactiveModuleLoader, SyncReloader, __version__
from reactivity.hmr.utils import load
from watchfiles import Change

from .utils import display_path, try_patch


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
