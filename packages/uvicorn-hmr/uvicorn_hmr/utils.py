from pathlib import Path

from typer import secho


def display_path(path: str | Path):
    """Format a file path for display, showing relative path if possible."""
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


def try_patch(app):
    """Try to patch the app for auto-reloading using fastapi-reloader."""
    try:
        from fastapi_reloader import patch_for_auto_reloading

        return patch_for_auto_reloading(app)

    except ImportError:
        secho(NOTE, fg="red")
        raise


def try_reload():
    """Try to send reload signal using fastapi-reloader."""
    try:
        from fastapi_reloader import send_reload_signal

        send_reload_signal()
    except ImportError:
        secho(NOTE, fg="red")
        raise
