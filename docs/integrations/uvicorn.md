---
icon: simple/fastapi
---

# ASGI — uvicorn-hmr

`uvicorn-hmr` loads ASGI applications through HMR. When the application changes, it shuts down the current Uvicorn server and starts another **in the same Python process**. ASGI lifespan shutdown and startup run again. This is not a connection-preserving app swap.

## Install and run

```sh
python -m pip install uvicorn-hmr fastapi
```

```python title="main.py"
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}
```

```sh
uvicorn-hmr main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. Change the message, save, and request the endpoint again. The response updates without replacing the Python process. Use an import target such as `main:app` or `mypackage.main:app`, not a filename such as `main.py:app`.

## CLI options

Run `uvicorn-hmr --help` for the installed version's options. This is a separate CLI, not a complete drop-in implementation of Uvicorn's options.

- `--host`, `--port`, `--log-level`, `--env-file`: passed to Uvicorn's configuration. The default host is `localhost` and port is `8000`. Using an env file requires Uvicorn's optional dotenv dependency.
- `--reload-include`: a file or directory path, repeatable; defaults to the current directory. The entry file is also included.
- `--reload-exclude`: a file or directory path, repeatable; defaults to `.venv`. These filters select modules eligible for HMR, not glob patterns.
- `--clear`: clear the terminal when relevant watched changes are detected.
- `--refresh`: enable HTML browser refresh through `fastapi-reloader`. Server reloading is already enabled without this flag.

The underlying watcher also has its own default file filters. Being under an included directory does not mean every file causes an application restart: changes must affect a loaded reactive module or a subscribed reactive filesystem dependency.

## Optional browser refresh

```sh
python -m pip install 'uvicorn-hmr[all]'
uvicorn-hmr main:app --refresh
```

This patches HTML responses to notify browser pages when the server reloads. A JSON response, such as the example above, has no HTML page to patch. See the [FastAPI example](https://github.com/promplate/hmr/tree/docs/examples/fastapi) for an HTML application and [fastapi-reloader reference](../references/fastapi-reloader.md).

## Configuration files read during lifespan

With `uvicorn-hmr 0.1.1.8` and `hmr 0.7.6.3`, a file read only during ASGI lifespan startup does **not** automatically become a reload dependency. Lifespan runs outside the reactive module-loading scope. Adding the file to `--reload-include`, or calling `track()` only inside lifespan, does not establish that dependency.

Declare the dependency at module top level in the HMR-loaded application module instead, before creating the app:

```python
from pathlib import Path
from reactivity.hmr.fs import track

CONFIG_FILE = Path(__file__).with_name("config.txt").resolve()
track(CONFIG_FILE)  # module top level, not inside the lifespan handler
```

Keep reading `CONFIG_FILE` in your lifespan handler as usual. The absolute path passed to `track()` must be covered by the watched paths and accepted by the watcher's file filters. With this declaration, changing the file invalidates the application module and restarts the server in the same process, rerunning lifespan shutdown and startup. Reading the file at module top level also establishes a dependency automatically.

Without that declaration, editing the application module still reruns lifespan and picks up the latest configuration; editing only the configuration file does not. See [issue #118](https://github.com/promplate/hmr/issues/118).

## Lifecycle limits

- Top-level code runs again when its module reloads. Heavy objects in unaffected modules can survive; objects assigned again in re-executed code are not automatically preserved.
- Server shutdown may close connections and wait for running requests. Do not assume WebSockets, streaming responses or in-flight work survive a reload.
- Manage resources with ASGI lifespan handlers or explicit disposal hooks. Avoid duplicate background workers and registrations.
- Use a clean process restart when changing native extensions or process-wide configuration. This runner is for development.
