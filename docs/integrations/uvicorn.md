# ASGI Integration (Uvicorn)

Use `uvicorn-hmr` for hot reloading ASGI applications including FastAPI, Starlette, Quart, and more.

## Installation

```sh
pip install uvicorn-hmr
```

For optional browser auto-refresh on page changes:

```sh
pip install uvicorn-hmr[all]
```

## Basic Usage

`uvicorn-hmr` is a drop-in replacement for `uvicorn`:

```sh
uvicorn-hmr main:app --reload
```

## With FastAPI

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.get("/items/{item_id}")
def read_item(item_id: int, q: str = None):
    return {"item_id": item_id, "q": q}
```

Run with:

```sh
uvicorn-hmr main:app --reload
```

Edit your code and see changes instantly without losing request state.

## Reactive ASGI

Combine with reactive primitives:

```python
from fastapi import FastAPI
from reactivity import signal, derived

app = FastAPI()
request_count = signal(0)

@app.middleware("http")
async def count_requests(request, call_next):
    request_count.set(request_count.get() + 1)
    response = await call_next(request)
    response.headers["X-Request-Count"] = str(request_count.get())
    return response

@app.get("/")
def read_root():
    return {"count": request_count.get()}
```

## Comparison with uvicorn --reload

| Feature             | `uvicorn --reload` | `uvicorn-hmr` |
| ------------------- | ------------------ | ------------- |
| Reload on change    | ✓                  | ✓             |
| State preservation  | ✗                  | ✓             |
| Fine-grained reload | ✗                  | ✓             |
| Reactive API        | ✗                  | ✓             |

## Performance

Since HMR uses fine-grained dependency tracking:

- **Faster reloads**: Only affected modules rerun
- **Lower overhead**: Unmodified code stays intact
- **Preserved state**: No need to reinitialize

## Advanced Configuration

See CLI options:

```sh
uvicorn-hmr --help
```

## Examples

See `examples/fastapi/` for a complete FastAPI+HMR example.

Learn more: [Flask Integration](./flask.md) | [MCP Integration](./mcp.md) | [Reactive Primitives](../reactive/signals.md)
