import sys
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import find_spec, module_from_spec
from pathlib import Path
from typing import Any
from weakref import WeakSet

__version__ = "0.0.4"

__all__ = "mcp_server", "run_with_hmr"


active_sessions = WeakSet()


_pending_patches = 0

_original_session_init = None  # the real one, captured once: re-reading it per arm would let a second arm capture the first arm's wrapper, and nothing would ever unwind


def patch_session_init():
    """Arm a one-shot capture of the next `ServerSession`. Overlapping arms are counted, not nested."""

    global _pending_patches, _original_session_init

    from mcp.server.session import ServerSession

    if _original_session_init is None:
        _original_session_init = ServerSession.__init__

        def capture_session_init(self: ServerSession, *args, **kwargs):
            _original_session_init(self, *args, **kwargs)  # type: ignore
            active_sessions.add(self)
            disarm()

        ServerSession.__init__ = capture_session_init

    _pending_patches += 1

    disarmed = False

    def disarm():  # idempotent per arm, so a captured session and an exiting transport cannot double-count
        nonlocal disarmed
        global _pending_patches, _original_session_init
        if disarmed:
            return
        disarmed = True
        _pending_patches -= 1
        if _pending_patches < 1 and _original_session_init is not None:  # the `is not None` is what narrows the type here
            ServerSession.__init__ = _original_session_init
            _original_session_init = None

    return disarm


def _install_notifier(base_app):
    """Give a `FastMCP` outer server a working list-changed channel, whichever SDK it sits on.

    On mcp 1.x that means capturing the session, which is per-connection and has the `send_*_list_changed`
    methods. On mcp 2.x the session is rebuilt per request, so a captured one is dead by the time we would
    notify — 2.x routes list-changed through a subscription bus instead, and fastmcp leaves that handler unset.
    """

    try:  # the era, decided by the one symbol that names it — a renamed subscription symbol below must not read as "this is 1.x"
        from mcp.server import MCPServer  # type: ignore  # noqa: F401
    except ImportError:  # mcp 1.x
        server = base_app._mcp_server  # noqa: SLF001
        original_run = server.run

        async def run_with_patched_session(*args, **kwargs):
            unpatch = patch_session_init()
            try:
                await original_run(*args, **kwargs)
            finally:
                unpatch()

        server.run = run_with_patched_session

        return notify_sessions

    from mcp.server.subscriptions import ListenHandler  # type: ignore
    from mcp.types import SubscriptionsListenRequestParams  # type: ignore

    server = base_app._mcp_server  # noqa: SLF001
    if "subscriptions/listen" in server._request_handlers:  # noqa: SLF001  # a fastmcp that grows its own listen handler owns the bus; ours would silently replace it
        raise NotImplementedError("mcp-hmr installs the `subscriptions/listen` handler this fastmcp now provides itself — please report this at https://github.com/promplate/hmr/issues")

    bus = _subscription_bus()
    server.add_request_handler("subscriptions/listen", SubscriptionsListenRequestParams, ListenHandler(bus))

    return _notify_bus(bus)


def _subscription_bus():
    from mcp.server.subscriptions import InMemorySubscriptionBus  # type: ignore

    return InMemorySubscriptionBus()


def _notify_bus(bus):
    """mcp 2.x publishes list-changed as events — the 2.x counterpart of `notify_sessions`."""

    from mcp.server.subscriptions import PromptsListChanged, ResourcesListChanged, ToolsListChanged  # type: ignore

    async def notify():
        for event in (ToolsListChanged(), ResourcesListChanged(), PromptsListChanged()):
            await bus.publish(event)

    return notify


async def notify_sessions():
    """1.x speaks list-changed straight over the session — the only channel a 1.x client has."""

    from asyncio import TaskGroup
    from contextlib import suppress

    from mcp.server.session import ServerSession

    async def notify_one(session: ServerSession):
        try:
            await session.send_tool_list_changed()
            await session.send_resource_list_changed()
            await session.send_prompt_list_changed()
        except Exception as e:
            with suppress(Exception):
                await session.send_log_message("warning", str(e))  # the raw exception would only fail to serialize later, in the transport's writer task

    async with TaskGroup() as tg:  # one slow client must not delay the others
        for session in list(active_sessions):
            tg.create_task(notify_one(session))


def proxy_backend():
    """For mcp 1.x, where nothing can swap a live server's contents: front the target with a FastMCP proxy."""

    from contextlib import contextmanager, suppress
    from inspect import signature

    from fastmcp import Client, FastMCP

    try:  # fastmcp 3 moved the proxy module and split `create_proxy` out of `FastMCP.as_proxy`
        from fastmcp.server import create_proxy  # type: ignore
        from fastmcp.server.providers.proxy import ProxyClient  # type: ignore

        fastmcp3 = True
    except ImportError:
        from fastmcp.server.proxy import ProxyClient

        fastmcp3 = False

    # fastmcp 3 dropped this kwarg together with the metadata it used to suppress
    no_meta: dict[str, Any] = {} if fastmcp3 else {"include_fastmcp_meta": False}

    # Handing `create_proxy` the target itself rather than a `ProxyClient` is what makes fastmcp 4 mirror the
    # front connection's protocol era onto the backend per request. Wrapping it first pins the backend to the
    # client's own era instead: `ProxyClient` defaults to `"legacy"`, which refuses every modern-envelope call,
    # and pinning `"auto"` fixes the calls but drops the back-channel a legacy front needs for sampling and
    # elicitation. Only fastmcp 4 negotiates eras at all; before it, `ProxyClient` is what forwards those.
    era_negotiated = "mode" in signature(Client.__init__).parameters

    def backend(app):
        return app if era_negotiated else ProxyClient(app)

    base_app = FastMCP(name="proxy", **no_meta)

    notify = _install_notifier(base_app)

    if fastmcp3:  # fastmcp 3 replaced `_mounted_servers` (and the per-manager copies older 2.x kept) with a single `providers` list

        @contextmanager
        def mount(app):
            base_app.mount(create_proxy(backend(app)))  # type: ignore
            provider = base_app.providers[-1]  # type: ignore
            try:
                yield
            finally:  # unmount
                base_app.providers.remove(provider)  # type: ignore

    else:

        @contextmanager
        def mount(app):
            base_app.mount(proxy := FastMCP.as_proxy(backend(app)), as_proxy=False)
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

    return base_app, mount, notify


def _swap_mount(base_app):
    """Both SDK eras look tools/resources/prompts up through the same three managers, so one swap serves both.

    Extensions, custom routes, middleware and lifespan stay the outer server's — only the registries move.
    """

    from contextlib import contextmanager

    registries = ("_tool_manager", "_tools"), ("_resource_manager", "_resources"), ("_resource_manager", "_templates"), ("_prompt_manager", "_prompts")

    def read(app):  # the containers themselves, not copies — while mounted, the target's live registries *are* the served ones
        return [getattr(getattr(app, manager), slot) for manager, slot in registries]

    # mcp 1.x keeps a second index of tool definitions that only refreshes on a *miss*, so a reload that changes an
    # existing tool's schema would keep validating calls against the old one. 2.x dropped it.
    tool_cache = getattr(getattr(base_app, "_mcp_server", None), "_tool_cache", None)

    def install(values):
        for (manager, slot), value in zip(registries, values, strict=True):
            setattr(getattr(base_app, manager), slot, value)
        if tool_cache is not None:
            tool_cache.clear()

    empty = read(base_app)  # captured before anything is mounted

    @contextmanager
    def mount(app):
        install(read(app))
        try:
            yield
        finally:  # unmount
            install(empty)

    return mount


def swap_backend_1x():
    """For mcp 1.x with no fastmcp to proxy with: the registries are plain dicts, so the same swap works.

    The target must be a `mcp.server.fastmcp.FastMCP` too. `list_changed` has to be forced on, because
    1.x derives capabilities from a `NotificationOptions` that every transport leaves defaulted to off.
    """

    from mcp.server.fastmcp import FastMCP
    from mcp.server.lowlevel.server import NotificationOptions

    base_app = FastMCP(name="proxy")
    server = base_app._mcp_server  # noqa: SLF001

    original_create_initialization_options = server.create_initialization_options

    def advertise_list_changed(notification_options=None, experimental_capabilities=None):  # noqa: ARG001  # every transport reaches the handshake through here, so one patch covers all three
        # forced rather than defaulted: a caller passing its own options would otherwise advertise `listChanged=False`
        # while we keep sending the notifications — and a default-constructed `NotificationOptions()` has them all off
        return original_create_initialization_options(NotificationOptions(prompts_changed=True, resources_changed=True, tools_changed=True), experimental_capabilities)

    server.create_initialization_options = advertise_list_changed

    return base_app, _swap_mount(base_app), _install_notifier(base_app)


def swap_backend():
    """For mcp 2.x, which has no mount or proxy: swap the target's registries into a stable outer server.

    Unlike the proxy backend this talks Python, not MCP, so the target must be an `MCPServer` too.
    Notifications go through the subscription bus, which only reaches clients that opened a
    `subscriptions/listen` stream — on the 2026-07-28 wire there is no other channel to reach them.
    """

    from mcp.server import MCPServer  # type: ignore

    bus = _subscription_bus()
    base_app = MCPServer("proxy", subscriptions=bus)

    return base_app, _swap_mount(base_app), _notify_bus(bus)


def pick_backend(app):
    """`app` is the load error when the very first load failed — then the environment is all we have to go on."""
    try:
        from mcp.server import MCPServer  # type: ignore

        if isinstance(app, MCPServer):  # `MCPServer` has no mount or proxy of its own, so the swap is the only thing that fits it
            return swap_backend()
        swap, sdk_server = swap_backend, MCPServer
    except ImportError:  # mcp 1.x, whose registries live in the same slots 2.x keeps them in
        from mcp.server.fastmcp import FastMCP

        swap, sdk_server = swap_backend_1x, FastMCP

    try:  # a load error lands here too — the proxy speaks MCP, so it hosts whichever target type turns up once the module is fixed
        return proxy_backend()
    except ImportError:  # the swap reads the SDK's own registries, so it cannot stand in for the proxy on anything else
        if not isinstance(app, sdk_server | Exception):
            raise ImportError(f"mcp-hmr cannot serve a {type(app).__name__} target without a fastmcp new enough to proxy with. Upgrade `fastmcp`, or use the SDK's own server class.") from None
        return swap()


def mcp_server(target: str):
    module, attr = target.rsplit(":", 1)

    from asyncio import Event, Lock, TaskGroup
    from contextlib import asynccontextmanager
    from traceback import print_exc

    from reactivity import async_effect, derived
    from reactivity.hmr.core import HMR_CONTEXT, AsyncReloader, _loader
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    base_app: Any = ...  # base_app / mount / notify are picked from the target itself on first load
    mount: Any = ...
    notify: Any = ...

    lock = Lock()

    async def notify_quietly():
        try:  # a notification is best-effort: failing to tell clients about a reload must not take the server down with it
            await notify()
        except Exception:
            print_exc()

    async def using(app, stop_event: Event, finish_event: Event):
        async with lock:
            try:
                with mount(app):
                    tg.create_task(notify_quietly())
                    await stop_event.wait()
            except Exception:  # an unmountable target (a wrong `attr`, or a backend the blind first-load pick got wrong) must not take the connection down with it
                print_exc()
            finally:
                finish_event.set()

    if Path(module).is_file():  # module:attr

        def load_app():
            if (mod := sys.modules.get(module)) is None:  # keyed by path, not a fixed name: two `mcp_server` contexts in one process must not share a module
                sys.modules[module] = mod = module_from_spec(ModuleSpec(module, _loader, origin=module))
            return getattr(mod, attr)

    else:  # path:attr

        def load_app():
            if (mod := sys.modules.get(module)) is None:
                spec = find_spec(module)
                if spec.loader is not _loader:  # type: ignore  # installed or outside the watched roots: not reloadable anyway, and `module_from_spec` alone would never execute it
                    return getattr(import_module(module), attr)
                # not `import_module`: a module whose body raises is purged from `sys.modules`, and the replacement built on retry would carry none of our subscriptions
                sys.modules[module] = mod = module_from_spec(spec)  # type: ignore
            return getattr(mod, attr)

    @derived(context=HMR_CONTEXT)
    def get_app():
        try:
            return load_app()
        except Exception:
            pass  # a failing load aborts before the attribute itself is ever read, so we'd only be subscribed to the load — retry now that it has settled, to also subscribe to the attribute a fixed module will define
        try:
            return load_app()
        except Exception as e:
            return e  # a broken load must still yield a *value*: a derived that never held one stays "unset", and its first success would notify nobody

    stop_event: Event | None = None
    finish_event: Event = ...  # type: ignore
    tg: TaskGroup = ...  # type: ignore

    @async_effect(context=HMR_CONTEXT, call_immediately=False)
    async def main():
        nonlocal stop_event, finish_event, base_app, mount, notify

        if stop_event is not None:
            stop_event.set()
            await finish_event.wait()

        app = get_app()
        if base_app is ...:  # a failed first load still needs a server to hand to the transport, so the client can connect and pick up the fix
            base_app, mount, notify = pick_backend(app)
        if isinstance(app, Exception):
            raise app

        tg.create_task(using(app, stop_event := Event(), finish_event := Event()))

    class Reloader(AsyncReloader):
        def __init__(self):
            super().__init__(".")  # this inserts a `ReactiveModuleFinder` at the front of `sys.meta_path`, and 0.7.6.3 offers no way to take it back out
            self.finder = sys.meta_path[0]
            self.error_filter.exclude_filenames.add(__file__)

        async def __aenter__(self):
            call_pre_reload_hooks()
            try:
                with self.error_filter:  # a broken module is reported and then waited on, exactly like every later reload
                    await main()
            finally:
                call_post_reload_hooks()
                tg.create_task(self.start_watching())

        async def __aexit__(self, *_):
            self.stop_watching()
            main.dispose()
            if self.finder in sys.meta_path:  # else every `mcp_server()` in a process leaves one behind
                sys.meta_path.remove(self.finder)
            if stop_event:
                stop_event.set()

    @asynccontextmanager
    async def _():
        nonlocal tg
        async with TaskGroup() as tg, Reloader():
            if base_app is ...:  # `pick_backend` raised, and the error filter reported it — there is no server to serve, and no edit that would make one
                raise SystemExit(1)
            yield base_app

    return _()


def _supported(runner, kwargs: dict):
    """Runner signatures drift across SDK versions (`log_level` only reached `run_stdio_async` in fastmcp 2.13, and `MCPServer`'s runners take a far narrower set).

    Filtering by signature also drops the `None` defaults argparse supplies, so the runner's own defaults win.
    """
    from inspect import Parameter, signature

    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    params = signature(runner).parameters
    if any(p.kind is Parameter.VAR_KEYWORD for p in params.values()):  # `run_async` forwards the rest to the transport, so filtering would silently drop them
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def _sdk_kwargs(runner, kwargs: dict, path_key: str):
    """The SDK's own runners name the route path per transport, and take no middleware."""
    if path := kwargs.get("path"):
        kwargs = kwargs | {path_key: path}
    if kwargs.get("middleware"):
        print("mcp-hmr: CORS is unavailable without fastmcp — the SDK's own runners take no middleware", file=sys.stderr)
    return _supported(runner, kwargs)


def _apply_settings(mcp, kwargs: dict):
    """mcp 1.x's own `FastMCP` takes no runner kwargs at all — host, port and route path come from `settings`."""
    host = kwargs.get("host")
    if host is not None and host not in ("127.0.0.1", "localhost", "::1"):
        # 1.x auto-enables DNS-rebinding protection when *constructed* on a loopback host, and we build the
        # outer server long before `--host` reaches here — so the allowlist would reject the very address the
        # socket now binds. Serving a non-loopback host is an explicit request; honor it rather than 421.
        mcp.settings.transport_security = None
    if log_level := kwargs.get("log_level"):
        from logging import getLogger

        getLogger().setLevel(log_level)  # 1.x configures logging while *constructing* the server, and its `basicConfig` is a no-op by the time `--log-level` reaches us

    settings = {key: kwargs.get(key) for key in ("host", "port", "log_level", "json_response", "stateless_http")}
    if path := kwargs.get("path"):
        settings |= {"streamable_http_path": path, "sse_path": path}
    for key, value in settings.items():
        if value is not None:
            setattr(mcp.settings, key, value)


async def run_with_hmr(target: str, log_level: str | None = None, transport="stdio", **kwargs):
    async with mcp_server(target) as mcp:
        if not hasattr(mcp, "run_async"):  # one of the SDK's own servers — its runners take no `log_level`, and mcp 1.x's take nothing at all
            kwargs |= {"log_level": log_level}
            if hasattr(mcp, "_mcp_server"):  # mcp 1.x's `FastMCP` reads host, port and paths off `settings` instead of from its runners — `MCPServer` names its inner server `_lowlevel_server`
                _apply_settings(mcp, kwargs)
            match transport:
                case "stdio":
                    return await mcp.run_stdio_async(**_sdk_kwargs(mcp.run_stdio_async, kwargs, ""))
                case "sse":
                    return await mcp.run_sse_async(**_sdk_kwargs(mcp.run_sse_async, kwargs, "sse_path"))
                case _:
                    return await mcp.run_streamable_http_async(**_sdk_kwargs(mcp.run_streamable_http_async, kwargs, "streamable_http_path"))
        kwargs |= {"log_level": log_level}
        match transport:
            case "stdio":
                await mcp.run_stdio_async(**_supported(mcp.run_stdio_async, kwargs | {"show_banner": False}))
            case "http" | "streamable-http":
                await mcp.run_http_async(**_supported(mcp.run_http_async, kwargs))
            case "sse":
                # for older FastMCP versions
                if hasattr(mcp, "run_sse_async"):
                    await mcp.run_sse_async(**_supported(mcp.run_sse_async, kwargs))
                else:
                    await mcp.run_http_async(transport="sse", **_supported(mcp.run_http_async, kwargs))
            case _:
                await mcp.run_async(transport, **_supported(mcp.run_async, kwargs))


def cli(argv: list[str] = sys.argv[1:]):
    from argparse import SUPPRESS, ArgumentParser

    parser = ArgumentParser("mcp-hmr", description="Hot Reloading for MCP Servers • Automatically reload on code changes")
    if sys.version_info >= (3, 14):
        parser.suggest_on_error = True
    parser.add_argument("target", help="The import path of the server instance. Supports module:attr and path:attr")
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

    if (cwd := str(Path.cwd())) not in sys.path:
        sys.path.append(cwd)

    if (file := Path(module_or_path := target[: target.rindex(":")])).is_file():
        sys.path.insert(0, str(file.parent))
    else:
        if "." in module_or_path:  # find_spec may cause implicit imports of parent packages
            from reactivity.hmr.core import patch_meta_path

            patch_meta_path()

        try:
            found = find_spec(module_or_path) is not None
        except ValueError:  # a name already in `sys.modules` but without a spec
            found = False
        except ImportError as e:  # a dotted name raises instead of returning `None` when a parent is missing — but importing it also runs the parents, so only the target's own absence belongs here
            if e.name is not None and module_or_path != e.name and not module_or_path.startswith(f"{e.name}."):  # `zqx_app.main` missing `zqx` is a missing dependency, not a missing target
                raise
            found = False

        if not found:
            parser.exit(1, f"The target '{module_or_path}' not found. Please provide a valid module name or a file path.")

    try:  # noqa: SIM105
        run(run_with_hmr(**kwargs))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
