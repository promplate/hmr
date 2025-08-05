"""Hot Module Replacement for Uvicorn."""

from typer import Typer

from .cli import main

# Create the main Typer app - this preserves the original uvicorn_hmr:app interface
app = Typer(help="Hot Module Replacement for Uvicorn", add_completion=False, pretty_exceptions_show_locals=False)

# Register the main command
app.command(no_args_is_help=True)(main)

# For backward compatibility and direct execution
if __name__ == "__main__":
    app()
