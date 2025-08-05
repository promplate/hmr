"""Allow uvicorn_hmr to be executed as a module with -m flag."""

from . import app

if __name__ == "__main__":
    app()