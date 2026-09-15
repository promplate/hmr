"""Install HMR only in the BentoML service worker that holds the model, via its circus command line."""

import argparse
from pathlib import Path

from .scope import load_manifest

WORKER_MODULE = "_bentoml_impl.worker.service"
# The entry service watcher; dependency services get `service_<name>` and are rejected below.
WATCHER = "service"


def _bootstrap(manifest: Path) -> str:
    return f"from bentoml_hmr.runtime import prepare; prepare({str(manifest)!r}); import runpy; runpy.run_module({WORKER_MODULE!r}, run_name='__main__')"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("service", help="a BentoML import string such as `app:Service`")
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    load_manifest(manifest)

    import bentoml.serving
    from _bentoml_impl.loader import load

    create_watcher = bentoml.serving.create_watcher
    selected = []

    def create_selected_watcher(name, args, **kwargs):
        if name == WATCHER:
            if WORKER_MODULE not in args:
                raise RuntimeError(f"watcher {name!r} does not run {WORKER_MODULE}: {args}")
            index = args.index(WORKER_MODULE)
            if args[index - 1] != "-m":
                raise RuntimeError(f"watcher {name!r} does not launch {WORKER_MODULE} with -m: {args}")
            if kwargs.get("numprocesses") != 1:
                raise RuntimeError(f"this runtime supports a single worker, got numprocesses={kwargs.get('numprocesses')!r}")
            # `-c` runs before the worker module, so `prepare` sees a process that has not imported
            # any BentoML source yet. Argument order is preserved, so click still parses the rest.
            args = [*args[: index - 1], "-c", _bootstrap(manifest), *args[index + 1 :]]
            selected.append(name)
        elif WORKER_MODULE in args:
            raise RuntimeError(f"a second BentoML worker watcher {name!r} would run without HMR")
        return create_watcher(name, args, **kwargs)

    bentoml.serving.create_watcher = create_selected_watcher
    try:
        service = load(args.service)
        service.serve_http(port=args.port)
    finally:
        bentoml.serving.create_watcher = create_watcher
        if len(selected) != 1:
            raise RuntimeError(f"expected exactly one HMR-enabled worker watcher, selected {selected}")
