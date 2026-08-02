"""
You don't need to understand what this code does.

This file implements a simple function to start a Sanic server with HMR support.

To use it, just copy this file to your project and call the `start_server` function with your Sanic app instance.
And then run this file with `hmr path/to/this/file.py`.

We haven't implement a separate integration for Sanic.
So you need to use this function to start the server with HMR support.
"""

from atexit import register, unregister
from multiprocessing import Process
from typing import cast

from sanic import Sanic


class ServerProcess(Process):
    def __init__(self, app: Sanic):
        super().__init__(daemon=True)
        self.app = app

    def run(self):
        # Run in a separate process to avoid signal conflicts
        self.app.run(host="localhost", port=8000, debug=False, auto_reload=False, single_process=True)

    def shutdown(self):
        if self.is_alive():
            self.terminate()
            self.join(timeout=1)
            if self.is_alive():
                self.kill()


def start_server(app: Sanic):
    global server

    print("* Running on http://localhost:8000")

    if server := cast("ServerProcess | None", globals().get("server")):
        unregister(server.shutdown)
        server.shutdown()

    server = ServerProcess(app)
    server.start()
    register(server.shutdown)


if __name__ == "__main__":
    from app import app

    start_server(app)


if __name__ == "__main__":
    from app import app

    start_server(app)
