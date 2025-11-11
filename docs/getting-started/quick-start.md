# Quick Start

Get your first HMR experience in minutes.

## Using the CLI

Replace `python` with `hmr` when running your script:

```sh
hmr app.py
```

Or with arguments:

```sh
hmr app.py --debug --port 8000
```

That's it! Your module now has hot reloading. Try editing `app.py` and watch the changes apply instantly.

## Running Modules

Like `python -m`:

```sh
hmr -m mypackage
```

## With ASGI Frameworks

If using FastAPI, Starlette, or other ASGI frameworks:

```sh
pip install uvicorn-hmr[all]
uvicorn-hmr main:app --reload
```

Or with Flask (via embedded server):

```sh
pip install fastapi-reloader
python app.py
```

## Using the Reactive API

For advanced use cases, use reactive primitives directly:

```python
from reactivity import signal, effect, derived

# Create a reactive signal
count = signal(0)

# Define a computed value
doubled = derived(lambda: count.get() * 2)

# Set up an effect
effect(lambda: print(f"Count: {count.get()}"))

# Update the signal
count.set(1)  # Prints: Count: 1
print(doubled.get())  # Prints: 2
```

Learn more in [Reactive Programming](../reactive/signals.md).

## Troubleshooting

- **Changes not reflecting?** Make sure you're editing files that `hmr` is tracking. See [CLI Reference](./cli.md) for include/exclude options.
- **Circular imports?** See [Troubleshooting](../reference/troubleshooting.md).
- **Need more control?** Check [Advanced Reactivity](../reactive/advanced.md).

Next: Explore [Reactive Programming](../reactive/signals.md) or jump to [HMR Integrations](../integrations/uvicorn.md).
