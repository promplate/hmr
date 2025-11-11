# Installation

Install the CLI and libraries you need.

- Install into an active virtual environment (recommended):

```sh
pip install hmr
```

- Run without installing (via uv/pnpm/bun wrappers):

```sh
uvx hmr path/to/entry.py
```

Optional integrations:

- ASGI apps (drop-in uvicorn replacement): `pip install uvicorn-hmr`
- MCP servers: `pip install mcp-hmr`
- Browser auto-refresh for FastAPI: `pip install fastapi-reloader`

Verify:

```sh
hmr --help
```

Notes:

- Prefer installing `hmr` inside the virtualenv used by your project to avoid cross-environment surprises.
- Use the integration packages listed above when running frameworks (`uvicorn-hmr`, `mcp-hmr`, `fastapi-reloader`).
