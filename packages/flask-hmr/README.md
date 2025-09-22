# Flask HMR

Hot Module Reloading for Flask applications, similar to uvicorn-hmr but for WSGI apps.

This package provides a CLI tool that replaces the standard `flask run` command with Hot Module Reloading capabilities.

## Installation

```bash
pip install flask-hmr
```

## Usage

Instead of using `flask run`, use:

```bash
flask-hmr app:app
```

Where `app:app` is your Flask application in the format `module:attribute`.

## Options

- `--host`: Host to bind to (default: localhost)
- `--port`: Port to bind to (default: 5000)
- `--reload-include`: Directories to watch for changes
- `--reload-exclude`: Directories to exclude from watching
- `--clear`: Clear terminal before restarting server
- `--env-file`: Environment file to load

## Example

```bash
flask-hmr --host 0.0.0.0 --port 8000 app:app
```