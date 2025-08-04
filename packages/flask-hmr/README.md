# flask-hmr

Hot Module Replacement for Flask applications, providing instant server reloading when your code changes.

## Installation

```bash
pip install flask-hmr
```

## Usage

Replace your `flask run` command with `flask-hmr`:

```bash
# Instead of: flask run
flask-hmr app:app

# With custom host and port
flask-hmr app:app --host 0.0.0.0 --port 8000

# Enable Flask debug mode
flask-hmr app:app --debug

# Clear terminal on reload
flask-hmr app:app --clear
```

## Features

- **Fast Hot Module Replacement**: Only reloads changed modules, not the entire application
- **Flask Integration**: Works seamlessly with Flask applications
- **Configurable**: Support for custom host, port, and debug settings
- **Smart Watching**: Automatically detects which files to watch based on your imports

## Example

Given a Flask app in `app.py`:

```python
from flask import Flask

app = Flask(__name__)

@app.route("/")
def hello():
    return "Hello, World!"

if __name__ == "__main__":
    app.run()
```

Start it with HMR:

```bash
flask-hmr app:app
```

Now when you modify your route handlers, view functions, or imported modules, the server will automatically reload with your changes.

## Command Line Options

- `slug`: The module and app variable (e.g., `app:app`, `myproject.wsgi:application`)
- `--host`: Host to bind to (default: 127.0.0.1)
- `--port`: Port to bind to (default: 5000)
- `--debug`: Enable Flask debug mode
- `--clear`: Clear terminal before restarting server
- `--reload-include`: Additional paths to watch for changes
- `--reload-exclude`: Paths to exclude from watching (default: .venv)

## Comparison with Standard Flask

| Feature | `flask run --reload` | `flask-hmr` |
|---------|---------------------|-------------|
| Reload Speed | Full process restart | Hot module replacement |
| Memory Usage | Higher (new process) | Lower (same process) |
| State Preservation | Lost on reload | Preserved where possible |
| Startup Time | Slower | Faster |

## Requirements

- Python >=3.12
- Flask >=2.0.0
- hmr >=0.5.0