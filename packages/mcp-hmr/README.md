# mcp-hmr

Hot Module Reloading runner for MCP servers (or any Python async entrypoint).

- Uses `hmr` to reload only changed modules and their dependents
- Keeps the main process alive; restarts your async server task inside
- Simple CLI: `mcp-hmr path.to.module:serve`

## Install

```sh
pip install mcp-hmr
```

## Usage

From the directory where your module is located:

```sh
mcp-hmr server:serve
```

The callable after the colon must be a function or coroutine function returning an awaitable that runs your server. On reload, `mcp-hmr` cancels the currently running task, reloads affected modules, and invokes the callable again.

### Options

- `reload-include` and `reload-exclude`: lists of paths similar to `uvicorn-hmr` (no glob patterns). Defaults: include current dir; exclude current virtualenv.
- `--clear`: clear terminal on restart.

## Demo

See `examples/mcp` in this repo for a minimal MCP-like stdio server that echoes a computed string; change code and see live reload.