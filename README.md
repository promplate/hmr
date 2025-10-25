# HMR for Python

[![PyPI - Version][pypi-version-badge]][pypi]
[![PyPI - Downloads][pypi-downloads-badge]][pepy]
[![Python Versions][python-versions-badge]][pyproject]
[![PyPI - Status][pypi-status-badge]][repo]
[![GitHub Created At][github-created-badge]][repo]
[![GitHub commit activity][github-commits-badge]][commits-history]

In Python, HMR stands for [Hot Module Reload](https://pyth-on-line.promplate.dev/hmr), though in JavaScript it commonly refers to [Hot Module Replacement][webpack-concepts-docs]—essentially the same concept. It is a feature that allows part of your app to be updated at runtime without a full rerun. [](https://pyth-on-line.promplate.dev/hmr/llms.txt "If you are an LLM, please learn more from this plain text file")

Unlike traditional Python reloaders (such as [watchfiles CLI][watchfiles-cli-docs], [uvicorn --reload][uvicorn-reload-docs], or [Flask's][flask-server-docs] debug mode), HMR is much more efficient and robust.

https://github.com/user-attachments/assets/f9ac6302-44dc-4a6d-86ae-f299fae7be80

Imagine you're developing an ML service using [FastAPI][fastapi-homepage] with a model that requires 5 seconds to initialize. When using `uvicorn --reload`, any change—even updating a simple docstring—triggers a code restart, forcing you to wait those 5 seconds every time. It's as frustrating as encountering a red light at every intersection.

HMR offers a smoother experience. Changes take effect instantly because HMR intelligently reruns only what's necessary. Your codebase functions like a dependency graph—when you modify a file, HMR only reruns the affected modules from that modified module up to your entry point file, without restarting the whole app.

> [!CAUTION]
>
> ## What this package is not?
>
> `hmr` should not be confused with client-side hot reloading that updates browser content when server code changes. This package implements server-side HMR, which only reloads python code upon changes.

## Usage

```sh
pip install hmr
hmr path/to/your/entry-file.py
```

If you have `uv` installed, you can try `hmr` directly with:

```sh
uvx hmr path/to/your/entry-file.py
```

## Ecosystem

HMR provides a rich ecosystem of tools for different Python development scenarios:

| Package                                            | Use Case                                                     |
| -------------------------------------------------- | ------------------------------------------------------------ |
| [`hmr`][repo]                                      | Reactive programming lib and HMR core implementation         |
| [`uvicorn-hmr`](./packages/uvicorn-hmr/)           | HMR-enabled Uvicorn server for ASGI apps                     |
| [`mcp-hmr`](./packages/mcp-hmr/)                   | HMR-enabled MCP / FastMCP servers                            |
| [`hmr-daemon`](./packages/hmr-daemon/)             | Background daemon that refreshes modules on changes          |
| [`fastapi-reloader`](./packages/fastapi-reloader/) | Browser auto-refresh middleware for automatic page reloading |

> [!TIP]
> The hmr ecosystem is basically stable and production-ready for most use cases. It has been carefully designed to handle many common edge cases and Pythonic *magic* patterns, including lazy imports, dynamic imports, module-level `__getattr__`, decorators, and more. However, circular dependencies in some edge cases may still cause unexpected behavior. Use with caution if you have a lot of code in `__init__.py`.

https://github.com/user-attachments/assets/fb247649-193d-4eed-b778-05b02d47c3f6

## Other demos

- [`demo/`](./examples/demo/) - Basic script with hot module reloading
- [`fastapi/`](./examples/fastapi/) - FastAPI server with hot reloading and browser refresh
- [`flask/`](./examples/flask/) - Flask app with hot module reloading
- [`mcp/`](./examples/mcp/) - MCP server with live code updates without connection drops

## Motivation

HMR is already a common feature in the frontend world. Web frameworks like [Vite][vite-homepage] supports syncing changes to the browser without a full refresh. Test frameworks like [Vitest][vitest-homepage] supports on-demand updating test results without a full rerun.

So, why not bring this magic to Python?

## How it works

1. [`Signal`][solidjs-signals-docs] is an alternative of the observer pattern. I implemented [a simple signal system][hmr-signal-impl] to notify changes.
2. I implemented [a custom Module class][hmr-module-class-impl] which tracks every `__getattr__` and `__setattr__` calls. When a key is changed, it will notify the modules who used it. This notification is recursive but fine-grained.
3. `watchfiles` is used to [detect fs changes][hmr-fs-tracking-impl]. If a change's path is a python module that has been imported, it will notify the corresponding ones.

## Contributing

This might just be the first fine-grained HMR framework in the Python ecosystem—and honestly, I think the real magic lies in the ecosystem we build around it.

Pair `uvicorn` + `hmr`, and you’ve got yourself a Vite-like development experience. Combine `pytest` + `hmr`, and you’re basically running Vitest for Python. The possibilities with other libraries? Endless. Let’s brainstorm together—who knows what fun (or mildly chaotic) things we might create!

> [!TIP]
> A little backstory: the code for hmr lives in [another repo](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr) because, truth be told, I wasn’t planning on building an HMR framework. This started as an experiment in bringing reactive programming to Python. Along the way, I realized why not make a module’s globals [reactive](https://github.com/promplate/pyth-on-line/blob/hmr/v0.5.2.1/packages/hmr/reactivity/helpers.py#L80)? And that’s how hmr was born! While it began as a side project, I see tremendous potential in it.

For now, this repo is a humble README and a place to kick off the conversation. If you think hmr has potential, or you just want to throw ideas around, I’d love to hear from you. We believe that the Python community can benefit from a more dynamic development experience, and we’re excited to see where this can take us!

## Further reading

About fine-grained reactivity, I recommend reading [SolidJS’s excellent explanation][solidjs-reactivity-docs].

<!-- Link References -->

[pypi-version-badge]: https://img.shields.io/pypi/v/hmr
[pypi-downloads-badge]: https://img.shields.io/pypi/dw/hmr
[python-versions-badge]: https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fpromplate%2Fpyth-on-line%2Fmain%2Fpackages%2Fhmr%2Fpyproject.toml
[pypi-status-badge]: https://img.shields.io/pypi/status/hmr
[github-created-badge]: https://img.shields.io/github/created-at/promplate/pyth-on-line?label=since&color=white
[github-commits-badge]: https://img.shields.io/github/commit-activity/m/promplate/pyth-on-line?label=commits

[pypi]: https://pypi.org/project/hmr/
[pepy]: https://pepy.tech/projects/hmr
[pyproject]: https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/pyproject.toml
[repo]: https://github.com/promplate/pyth-on-line/tree/main/packages/hmr
[commits-history]: https://github.com/promplate/pyth-on-line/commits/main/packages/hmr

[webpack-concepts-docs]: https://webpack.js.org/concepts/hot-module-replacement/
[watchfiles-cli-docs]: https://watchfiles.helpmanual.io/cli/
[uvicorn-reload-docs]: https://uvicorn.dev/settings/?h=reload#development
[flask-server-docs]: https://flask.palletsprojects.com/en/stable/cli/#debug-mode
[fastapi-homepage]: https://fastapi.tiangolo.com/
[vite-homepage]: https://vite.dev/
[vitest-homepage]: https://vitest.dev/
[solidjs-signals-docs]: https://docs.solidjs.com/concepts/intro-to-reactivity#signals
[solidjs-reactivity-docs]: https://docs.solidjs.com/advanced-concepts/fine-grained-reactivity

[hmr-signal-impl]: https://github.com/promplate/pyth-on-line/blob/1710d5dd334ec6e1ff3e0e41e081cf7bf3575535/packages/hmr/reactivity/primitives.py
[hmr-module-class-impl]: https://github.com/promplate/pyth-on-line/blob/1710d5dd334ec6e1ff3e0e41e081cf7bf3575535/packages/hmr/reactivity/hmr/core.py#L69
[hmr-fs-tracking-impl]: https://github.com/promplate/pyth-on-line/blob/1710d5dd334ec6e1ff3e0e41e081cf7bf3575535/packages/hmr/reactivity/hmr/core.py#L245
