from pathlib import Path
from typing import TYPE_CHECKING

from typer import Argument, Typer, secho

app = Typer(help="Hot Module Replacement for Uvicorn", add_completion=False)


@app.command(no_args_is_help=True)
def main(
    slug: str = Argument("main:app"),
    reload_include: str = str(Path.cwd()),
    reload_exclude: str = ".venv",
    host: str = "localhost",
    port: int = 8000,
    env_file: Path | None = None,
    log_level: str | None = "info",
):
    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        exit(1)
    module, attr = slug.split(":")

    fragment = Path(module.replace(".", "/"))

    if (path := fragment.with_suffix(".py")).is_file() or (path := fragment / "__init__.py").is_file():
        file = path.resolve()
    else:
        secho("Module", fg="red", nl=False)
        secho(f" {module} ", fg="yellow", nl=False)
        secho("not found.", fg="red")
        exit(1)

    import sys
    from atexit import register
    from importlib import import_module
    from threading import Event, Thread

    from reactivity import memoized_method
    from reactivity.hmr.core import SyncReloader
    from uvicorn import Config, Server

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
            server.run()
            finish.set()

        Thread(target=run_server, daemon=True).start()

        def stop_server():
            server.should_exit = True
            finish.wait()

    class Reloader(SyncReloader):
        def __init__(self):
            super().__init__(str(file), {reload_include}, {reload_exclude})
            self.error_filter.exclude_filenames.add(__file__)  # exclude error stacks within this file
            self.module = import_module(module)

        @memoized_method
        def run_entry_file(self):
            with self.error_filter:
                stop_server()
                start_server(getattr(self.module, attr))

    Reloader().keep_watching_until_interrupt()
