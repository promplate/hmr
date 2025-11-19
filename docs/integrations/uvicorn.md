# ASGI — uvicorn-hmr

uvicorn-hmr runs ASGI apps with HMR-enabled module loading, reducing restart time by re-executing only changed modules and their dependents.

## Install

```sh
pip install uvicorn-hmr
# optional browser auto-refresh
pip install uvicorn-hmr[all]
```

## Run

```sh
uvicorn-hmr main:app
# supports common uvicorn options; see `uvicorn-hmr --help`
```

## CLI Options

**Standard uvicorn options:** `host`, `port`, `log-level`, `env-file` work the same as in uvicorn.

**HMR-specific options:**

- `--refresh`: Auto-refresh HTML pages in the browser when the server reloads (requires `fastapi-reloader` and/or `uvicorn-hmr[all]`)
- `--clear`: Clear the terminal before each reload (like Vite's default behavior)
- `--reload-include`: Paths to watch (defaults to current directory)
- `--reload-exclude`: Paths to ignore

**Note:** Unlike uvicorn, `reload-include` and `reload-exclude` accept paths only (not patterns). HMR automatically tracks file system access through reactive FS tracking.

## What to expect

- Reloads on file changes without full process restart.
- Preserves in-memory objects in unaffected modules and when using reactive primitives.
- Not a full substitute for process restarts; some resources need a clean restart.

## Comparison with `uvicorn --reload`

| Feature             | uvicorn --reload | uvicorn-hmr        |
| ------------------- | ---------------- | ------------------ |
| Reload on change    | ✓                | ✓                  |
| Preserve some state | ✗                | ✓ (best effort)    |
| Fine-grained reload | ✗                | ✓                  |
| Reactive primitives | ✗                | ✓                  |
| Browser refresh     | ✗                | ✓ (with --refresh) |

## Caveats

- Top-level init runs again when a module is re-executed; keep heavy init in modules you rarely edit.
- C extensions, process-level singletons, and libraries assuming a fresh process may not be safe to hot-reload.
- Circular imports or module identity checks can behave unexpectedly after in-place reloads.

## Best practices

- Put DB clients, ML models, or other heavy resources behind factories or in stable modules.
- Use signals/derived/effects for runtime-changing state.
- Register disposal hooks for cleanup before a module is re-executed.
- Keep modules small and initialization idempotent.

## Debugging

- Run `uvicorn-hmr --help` for options.
- If behavior looks wrong, isolate in a minimal example; restart the process to reset global/native state when needed.

## Example

```python
# main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}
```

```sh
uvicorn-hmr main:app
# with browser auto-refresh (requires `fastapi-reloader`):
uvicorn-hmr main:app --refresh
```

See [examples/fastapi/](../../examples/fastapi/ "FastAPI example — examples/fastapi/") for a concrete example (how to run and what to observe) and [docs/reactive/](../reactive/index.md "Reactive Programming Basics") for reactive primitives that integrate with HMR.
