---
icon: simple/flask
---

# Flask

There is no dedicated Werkzeug integration in this repository. The [Flask example](https://github.com/promplate/hmr/tree/docs/examples/flask) provides a small threaded development runner instead.

## Run the repository example

From a checkout of the `docs` branch:

```sh
cd examples/flask
uv run hmr start.py
```

Alternatively, install `hmr` and `flask` in your environment and run `hmr start.py` from that directory. Open <http://localhost:5000/>. Edit the return value of `index()` in `app.py`, save, and request the page again. The response changes in the same Python process.

## Use the runner in your project

Copy [`start.py`](https://github.com/promplate/hmr/blob/docs/examples/flask/start.py) alongside your `app.py`. It imports `app` from `app.py`:

```python title="app.py"
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Hello from Flask!"
```

Run `hmr start.py`, not `python start.py`. On reload, the runner shuts down the previous Werkzeug server and starts a new server thread with the updated Flask app. Its `atexit` handler handles normal process exit.

Do not add a blocking `app.run()` to this entry path. The plain HMR CLI starts its watcher only after the entry file returns; the example returns promptly because the server runs in a background thread.

This is a development example, not a production WSGI server or a promise of uninterrupted requests. Application initialization can run again, request connections may end, and state in re-executed modules needs explicit lifecycle management. Browser refresh is manual.
