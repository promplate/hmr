# sanic-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/sanic-hmr)](https://pypi.org/project/sanic-hmr/)

This package provides hot module reloading (HMR) for [`sanic`](https://github.com/sanic-org/sanic).

It uses [`watchfiles`](https://github.com/samuelcolvin/watchfiles) to detect FS modifications,
re-executes the corresponding modules with [`hmr`](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr) and restart the server (in the same process).

**HOT** means the main process never restarts, and reloads are fine-grained (only the changed modules and their dependent modules are reloaded).
Since the python module reloading is on-demand and the server is not restarted on every save, it is much faster than Sanic's built-in `--auto-reload` option.

## Why?

1. When you use `sanic app.py --auto-reload`, it restarts the whole process on every file change, but restarting the whole process is unnecessary:
   - There is no need to restart the Python interpreter, neither all the 3rd-party packages you imported.
   - Your changes usually affect only one single file, the rest of your application remains unchanged.
2. `hmr` tracks dependencies at runtime, remembers the relationships between your modules and only reruns necessary modules.
3. So you can save a lot of time by not restarting the whole process on every file change. You can see a significant speedup for debugging large applications.
4. Although magic is involved, we thought and tested them very carefully, so everything works just as-wished.
   - Your lazy loading through module-level `__getattr__` still works
   - Your runtime imports through `importlib.import_module` or even `__import__` still work
   - Even valid circular imports between `__init__.py` and sibling modules still work
   - Fine-grained dependency tracking in the above cases still work
   - Decorators still work, even meta programming hacks like `getsource` calls work too
   - Standard dunder metadata like `__name__`, `__doc__`, `__file__`, `__package__` are correctly set

Normally, you can replace `sanic app.py --auto-reload` with `sanic-hmr app:app` and everything will work as expected, with a much faster refresh experience.

## Installation

```sh
pip install sanic-hmr
```

## Usage

Replace

```sh
sanic app.py --auto-reload
```

or

```sh
python -m sanic app.app --auto-reload
```

with

```sh
sanic-hmr app:app
```

Everything will work as-expected, but with **hot** module reloading.

## CLI Arguments

This package supports the most common Sanic server configuration options:

- `--host`: Host to listen on (default: `localhost`)
- `--port`: Port to listen on (default: `8000`)
- `--workers`: Number of worker processes (default: `1` - forced for HMR compatibility)
- `--debug`: Enable debug mode (default: `False`)
- `--access-log/--no-access-log`: Enable/disable access log (default: enabled)
- `--clear`: Clear the terminal before each reload (default: `False`)

The behavior of `reload_include` and `reload_exclude` is:

1. Only file or directory paths are allowed; patterns will be treated as literal paths.
2. Currently only supports hot-reloading Python source files.
3. If you do not provide `reload_include`, the current directory is included by default; if you do provide it, only the specified paths are included. The same applies to `reload_exclude`.

## Limitations

- **Single worker only**: HMR requires running in single-worker mode. The `--workers` argument is ignored and forced to 1.
- **Python files only**: Only Python source files are watched for changes.
- **No WebSocket auto-reload**: Unlike some development servers, this doesn't automatically refresh browser pages.

## Example

Create a simple Sanic app:

```python
# app.py
from sanic import Sanic, response

app = Sanic("MyApp")

@app.route("/")
async def hello_world(request):
    return response.text("Hello, world!")

@app.route("/test")
async def test_route(request):
    return response.json({"message": "This is a test route"})
```

Then run with hot module reloading:

```sh
sanic-hmr app:app
```

Now when you modify `app.py`, the server will automatically reload only the changed modules, providing much faster development cycles compared to full process restarts.