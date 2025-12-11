# FastAPI Example

This example demonstrates how to use `uvicorn-hmr` with a FastAPI application.

## How to Run

After installing dependencies, run the following command in this directory:

```sh
uvicorn-hmr main:app
```

This will start a FastAPI development server with HOT-reloading enabled.

## What to Observe

Once the server is started, you can access the Swagger API docs at `http://localhost:8000/docs`.

- Trigger the endpoints `/hello` and `/woof`.
- Try modifying the response in `a.py` or `b.py`.
- Rerun the endpoints in the documentation to see the changes applied instantly without restarting the server.

Also, if you add the `--refresh` option (note that it is not the same as `--reload` in standard Uvicorn):

```sh
uvicorn-hmr main:app --refresh
```

It will enable the `fastapi-reloader` feature, which refreshes the browser automatically when changes are detected in the code.

Try modifying the route names or add some route handlers, and you will see the docs page refresh automatically to reflect your changes.
