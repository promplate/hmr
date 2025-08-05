from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

from reactivity.hmr.core import ReactiveModule, is_relative_to_any
from uvicorn import Config, Server

from .utils import try_reload

if TYPE_CHECKING:
    from uvicorn._types import ASGIApplication


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
