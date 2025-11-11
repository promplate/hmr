# Flask

Use HMR with Flask applications for instant code reload without restarting.

## Installation

```sh
pip install hmr
```

For a full dev experience with browser auto-refresh:

```sh
pip install hmr fastapi-reloader
```

## Basic Usage

Instead of `python app.py`, use:

```sh
hmr app.py
```

Flask's development server will automatically reload when you edit your code.

## With Development Mode

Combine with Flask's debug mode:

```sh
export FLASK_ENV=development
export FLASK_DEBUG=1
hmr app.py
```

Or in your code:

```python
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

if __name__ == '__main__':
    app.run(debug=True)
```

Then run:

```sh
hmr app.py
```

## Reactive State

You can also use reactive primitives in your Flask app:

```python
from flask import Flask, jsonify
from reactivity import signal, derived

app = Flask(__name__)
counter = signal(0)

@app.route('/count')
def get_count():
    return jsonify({"count": counter.get()})

@app.route('/increment')
def increment():
    counter.set(counter.get() + 1)
    return jsonify({"count": counter.get()})
```

State in signals will persist across reloads.

## Key Differences from Flask Debug Mode

| Feature             | Flask Debug | HMR              |
| ------------------- | ----------- | ---------------- |
| Reload on change    | ✓           | ✓                |
| State preservation  | ✗           | ✓                |
| Global scope rerun  | ✓           | ✗ (fine-grained) |
| Reactive primitives | ✗           | ✓                |

## Examples

See `examples/flask/` for a complete Flask+HMR example.

Learn more: [CLI Reference](../getting-started/cli.md) | [Reactive Primitives](../reactive/signals.md) | [ASGI Integration](./uvicorn.md)
