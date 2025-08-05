import sys
from atexit import register
from pathlib import Path
from typing import Annotated

from typer import Argument, Option, secho

from .reloader import UvicornReloader
from .server import ServerManager


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
    """Hot Module Replacement for Uvicorn."""
    if reload:
        secho("\nWarning: The `--reload` flag is deprecated in favor of `--refresh` to avoid ambiguity.\n", fg="yellow")
        refresh = reload  # For backward compatibility, map reload to refresh

    if ":" not in slug:
        secho("Invalid slug: ", fg="red", nl=False)
        secho(slug, fg="yellow")
        exit(1)

    module, attr = slug.split(":")
    fragment = module.replace(".", "/")

    # Find the module file
    file: Path | None
    is_package = False
    for path in ("", *sys.path):
        if (file := Path(path, f"{fragment}.py")).is_file():
            is_package = False
            break
        if (file := Path(path, fragment, "__init__.py")).is_file():
            is_package = True
            break
    else:
        file = None

    if file is None:
        secho("Module", fg="red", nl=False)
        secho(f" {module} ", fg="yellow", nl=False)
        secho("not found.", fg="red")
        exit(1)

    if module in sys.modules:
        return secho(
            f"It seems you've already imported `{module}` as a normal module. You should call `reactivity.hmr.core.patch_meta_path()` before it.",
            fg="red",
        )

    # Add current directory to sys.path if not already there
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    # Create server manager and reloader
    server_manager = ServerManager(host, port, env_file, log_level, refresh)
    reloader = UvicornReloader(file, module, attr, reload_include, reload_exclude, is_package, refresh, clear, slug, server_manager)

    # Register cleanup function
    @register
    def cleanup():
        server_manager.stop_server()

    # Start watching for changes
    reloader.keep_watching_until_interrupt()
    server_manager.stop_server()
