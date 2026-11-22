# Sanic Example

This example demonstrates how to use HMR with a Sanic application.

## How to Run

After installing dependencies, run the following command in this directory:

```sh
hmr app.py
```

This will start a Sanic development server with HOT-reloading enabled.

## What to Observe

Once the server is running, you can access the application at `http://localhost:8000`.

- Visit `http://localhost:8000/a` and `http://localhost:8000/b`.
- Try modifying `b.py` and refresh the browser to see the changes applied instantly (without rerunning `sleep(1)` in `a.py`).
- Everything else should work as expected too. You will find your development experience much smoother than just using `sanic app:app --dev`.

> [!NOTE]
> Unlike [the FastAPI example](../fastapi/), we haven't implement a separate integration for Sanic.
> If you know Sanic well, you are welcome to contribute an integration similar to `uvicorn-hmr`.

## Available Endpoints

- `/` - Main app endpoint with route information
- `/a` - Blueprint A endpoint  
- `/b` - Blueprint B endpoint
- `/b/test` - Test endpoint from Blueprint B