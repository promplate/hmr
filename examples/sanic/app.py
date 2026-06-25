import time

from a import a
from b import b
from sanic import Sanic
from sanic.response import json

# Create a dynamic app name to avoid conflicts during HMR
app_name = f"SanicExample_{int(time.time())}"
app = Sanic(app_name)

app.blueprint(a)
app.blueprint(b)


@app.route("/")
async def index(_):
    return json({"message": "Hello from main Sanic app!", "routes": ["/a", "/b", "/b/test"]})


if __name__ == "__main__":
    from start import start_server

    start_server(app)
