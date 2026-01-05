import sys
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import find_spec, module_from_spec
from pathlib import Path

__version__ = "0.0.3.2"

__all__ = "mcp_server", "run_with_hmr"


def _resolve_watch_path(module_or_path: str) -> str:
    """Resolve a stable watch directory for hot reload.

    Why this exists:
        `AsyncReloader` expects a non-empty, valid path to watch. In mcp-hmr 0.0.3.2
        the watcher was initialized with an empty string, which can result in no
        files being watched (or errors) depending on the platform / watcher backend.

    Strategy:
        - If the target is a file path, watch its parent directory.
        - If the target is an importable module, watch the directory containing the
          module's file (or package __init__.py).
        - Fall back to the current working directory.
    """
    # path:attr target
    p = Path(module_or_path)
    if p.is_file():
        return str(p.resolve().parent)

    # Some callers may provide a directory path directly; watch it as-is.
    if p.is_dir():
        return str(p.resolve())

    # module:attr target
    try:
        spec = find_spec(module_or_path)
    except (ImportError, ModuleNotFoundError, TypeError, ValueError):
        spec = None

    if spec is not None:
        # For built-in / frozen modules, spec.origin can be strings like "built-in".
        # Use has_location to ensure origin is a real filesystem location.
        if getattr(spec, "has_location", False) and spec.origin:
            origin_path = Path(spec.origin)
            if origin_path.is_file():
                # For packages, origin points at __init__.py; for modules, origin is the .py file.
                return str(origin_path.resolve().parent)

        # Namespace packages may have no origin; use their search location.
        locations = list(spec.submodule_search_locations or ())
        if locations:
            return str(Path(locations[0]).resolve())

    return str(Path.cwd())


def mcp_server(target: str):
    module, attr = target.rsplit(":", 1)

    from asyncio import Event, Lock, TaskGroup
    from contextlib import asynccontextmanager, contextmanager, suppress

    import mcp.server
    from fastmcp import FastMCP
    from fastmcp.server.proxy import ProxyClient
    from reactivity import async_effect, derived
    from reactivity.hmr.core import HMR_CONTEXT, AsyncReloader, _loader
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    base_app = FastMCP(name="proxy", include_fastmcp_meta=False)

    @contextmanager
    def mount(app: FastMCP | mcp.server.FastMCP):
        base_app.mount(proxy := FastMCP.as_proxy(ProxyClient(app)), as_proxy=False)
        try:
            yield
        finally:  # unmount
            for mounted_server in list(base_app._mounted_servers):  # noqa: SLF001
                if mounted_server.server is proxy:
                    base_app._mounted_servers.remove(mounted_server)  # noqa: SLF001
                    # for older FastMCP versions
                    with suppress(AttributeError):
                        base_app._tool_manager._mounted_servers.remove(mounted_server)  # type: ignore  # noqa: SLF001
                        base_app._resource_manager._mounted_servers.remove(mounted_server)  # type: ignore  # noqa: SLF001
                        base_app._prompt_manager._mounted_servers.remove(mounted_server)  # type: ignore  # noqa: SLF001
                    break

    lock = Lock()

    async def using(app: FastMCP | mcp.server.FastMCP, stop_event: Event, finish_event: Event):
        async with lock:
            with mount(app):
                await stop_event.wait()
                finish_event.set()

    if Path(module).is_file():  # module:attr

        @derived(context=HMR_CONTEXT)
        def get_app():
            if (mod := sys.modules.get("server_module")) is None:
                sys.modules["server_module"] = mod = module_from_spec(ModuleSpec("server_module", _loader, origin=module))
            return getattr(mod, attr)

    else:  # path:attr

        @derived(context=HMR_CONTEXT)
        def get_app():
            return getattr(import_module(module), attr)

    stop_event: Event | None = None
    finish_event: Event = ...  # type: ignore
    tg: TaskGroup = ...  # type: ignore

    @async_effect(context=HMR_CONTEXT, call_immediately=False)
    async def main():
        nonlocal stop_event, finish_event

        if stop_event is not None:
            stop_event.set()
            await finish_event.wait()

        app = get_app()

        tg.create_task(using(app, stop_event := Event(), finish_event := Event()))

    class Reloader(AsyncReloader):
        def __init__(self):
            super().__init__(_resolve_watch_path(module))
            self.error_filter.exclude_filenames.add(__file__)

        async def __aenter__(self):
            call_pre_reload_hooks()
            try:
                await main()
            finally:
                call_post_reload_hooks()
                tg.create_task(self.start_watching())

        async def __aexit__(self, *_):
            self.stop_watching()
            main.dispose()
            if stop_event:
                stop_event.set()

    @asynccontextmanager
    async def _():
        nonlocal tg
        async with TaskGroup() as tg, Reloader():
            yield base_app

    return _()


async def run_with_hmr(target: str, log_level: str | None = None, transport="stdio", **kwargs):
    async with mcp_server(target) as mcp:
        match transport:
            case "stdio":
                await mcp.run_stdio_async(show_banner=False, log_level=log_level)
            case "http" | "streamable-http":
                await mcp.run_http_async(log_level=log_level, **kwargs)
            case "sse":
                # for older FastMCP versions
                if hasattr(mcp, "run_sse_async"):
                    await mcp.run_sse_async(log_level=log_level, **kwargs)  # type: ignore
                else:
                    await mcp.run_http_async(transport="sse", log_level=log_level, **kwargs)
            case _:
                await mcp.run_async(transport, log_level=log_level, **kwargs)  # type: ignore


def cli(argv: list[str] = sys.argv[1:]):
    from argparse import SUPPRESS, ArgumentParser

    parser = ArgumentParser("mcp-hmr", description="Hot Reloading for MCP Servers • Automatically reload on code changes")
    if sys.version_info >= (3, 14):
        parser.suggest_on_error = True
    parser.add_argument("target", help="The import path of the FastMCP instance. Supports module:attr and path:attr")
    parser.add_argument("-t", "--transport", choices=["stdio", "http", "sse", "streamable-http"], default="stdio", help="Transport protocol to use (default: stdio)")
    parser.add_argument("-l", "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], type=str.upper, default=None)
    parser.add_argument("--host", default="localhost", help="Host to bind to for http/sse transports (default: localhost)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to for http/sse transports (default: 8000)")
    parser.add_argument("--path", default=None, help="Route path for the server (default: /mcp for http, /sse for sse)")
    parser.add_argument("--stateless", action="store_true", help="Shortcut for `stateless_http=True` and `json_response=True`")
    parser.add_argument("--no-cors", action="store_true", help="Disable CORS (the default is to enable CORS for all origins)")
    parser.add_argument("--version", action="version", version=f"mcp-hmr {__version__}", help=SUPPRESS)

    if not argv:
        parser.print_help()
        return

    args = parser.parse_args(argv)

    target: str = args.target

    if ":" not in target[1:-1]:
        parser.exit(1, f"The target argument must be in the format 'module:attr' (e.g. 'main:app') or 'path:attr' (e.g. './path/to/main.py:app'). Got: '{target}'")

    kwargs = args.__dict__

    if kwargs.pop("stateless"):
        if args.transport != "http":
            parser.exit(1, "--stateless can only be used with the http transport.")
        args.json_response = True
        args.stateless_http = True

    if kwargs.pop("no_cors"):
        if args.transport != "http":
            parser.exit(1, "--no-cors can only be used with the http transport.")
    elif args.transport == "http":
        from starlette.middleware import Middleware, cors

        args.middleware = [Middleware(cors.CORSMiddleware, allow_origins="*", allow_methods="*", allow_headers="*", expose_headers="*")]

    if args.transport != "stdio":
        args.uvicorn_config = {"timeout_graceful_shutdown": 1e-100}  # align with upstream behavior but prevent error messages when no clients are connected

    from asyncio import run
    from contextlib import suppress

    if (cwd := str(Path.cwd())) not in sys.path:
        sys.path.append(cwd)

    if (file := Path(module_or_path := target[: target.rindex(":")])).is_file():
        sys.path.insert(0, str(file.parent))
    else:
        if "." in module_or_path:  # find_spec may cause implicit imports of parent packages
            from reactivity.hmr.core import patch_meta_path

            patch_meta_path()

        if find_spec(module_or_path) is None:
            parser.exit(1, f"The target '{module_or_path}' not found. Please provide a valid module name or a file path.")

    with suppress(KeyboardInterrupt):
        run(run_with_hmr(**kwargs))


if __name__ == "__main__":
    cli()
