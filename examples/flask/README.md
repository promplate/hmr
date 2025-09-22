# Flask Example

This example demonstrates how to use HMR with a Flask application.

## How to Run

### Option 1: Using flask-hmr CLI (Recommended)

After installing dependencies, run the following command in this directory:

```sh
flask-hmr app:app
```

Or with custom options:

```sh
flask-hmr --host 0.0.0.0 --port 8080 --clear app:app
```

### Option 2: Using the original HMR approach

```sh
hmr app.py
```

### Option 3: Using the custom start.py approach (Legacy)

```sh
hmr start.py
```

## Available flask-hmr CLI Options

- `--host`: Host to bind to (default: localhost)
- `--port`: Port to bind to (default: 5000)
- `--reload-include`: Directories to watch for changes (default: current directory)
- `--reload-exclude`: Directories to exclude from watching (default: .venv)
- `--clear`: Clear terminal before restarting server
- `--env-file`: Environment file to load

## What to Observe

Once the server is running, you can access the application at `http://localhost:5000`.

- Visit `http://localhost:5000/a` and `http://localhost:5000/b`.
- Try modifying `b.py` and refresh the browser to see the changes applied instantly (without rerunning `sleep(1)` in `a.py`).
- Everything else should work as expected too. You will find your development experience much smoother than just using `flask run --reload`.

## What's New

The `flask-hmr` CLI provides a Flask-specific hot module reloading experience similar to uvicorn-hmr but for WSGI applications. It integrates seamlessly with the existing HMR system while providing a familiar Flask-like interface.
