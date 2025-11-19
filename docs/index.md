---
icon: lucide/spade
hide:
  - toc
title: Introduction
---

# What is HMR _- An Engine, a Tool, an Ecosystem._

[HMR](https://pypi.org/project/hmr/ "this library") provides a pythonic, flexible, progressive-yet-intuitive reactive programming **engine**/**framework**, and on top of that, a fine-grained, on-demand hot-reload **tool**.

- [x] The reactive engine implements high-performance **push-pull reactivity**[^1][^2], letting you elegantly [do reactive programming](reactive/index.md){ data-preview } in Python
- [x] The hot-reloading tools are **drop-in replacements** for `python` CLI, `uvicorn`, and `mcp / fastmcp` integration, boosting your development with or without frameworks

!!! tip

    Most features are usable independently. **Any** user can use one or more of the features above to improve development efficiency _(not exaggerating, this library improved my debugging efficiency by 10x-100x)_

This project supports Python 3.12+, and is compatible with alternative Python runtimes like [Pyodide](https://github.com/pyodide/pyodide "Pyodide — Python in the browser"), [GraalPy](https://github.com/oracle/graalpython "GraalPython — Python runtime on GraalVM"), and [RustPython](https://github.com/RustPython/RustPython "RustPython — Python interpreter in Rust"). It supports both asyncio and trio, and is well-tested in 3.12-3.14.

??? info "Why Python 3.12+?"

    The project requires at least Python 3.12 because the I values static typing, and 3.12 started supporting some convenient new features in static typing. **If you don't mind these, you can easily support down to 3.10 or even 3.8 with minor changes.**

    ---
    For the same reason, [PyPy](https://github.com/pypy/pypy "PyPy: A fast, compliant Python implementation with a JIT compiler") is not supported as for now [latest PyPy](https://pypy.org/posts/2025/07/pypy-v7320-release.html "PyPy v7.3.20 release") only supports up to Python 3.11.

## Links

- Repo: Most code lives in [promplate/hmr](https://github.com/promplate/hmr "hmr — real hot-module reload for Python"), except the [core library](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr "hmr core package — promplate/pyth-on-line/packages/hmr") and [tests](https://github.com/promplate/pyth-on-line/tree/main/tests/py "hmr tests — promplate/pyth-on-line/tests/py") are temporarily in another repo
- Docs: [Docs](https://hmr.promplate.dev/ "HMR — Official documentation") / [Interactive Docs (under construction)](https://py3.online/hmr "An interactive online notebook built with Pyodide py3.online/hmr") / [llms.txt](https://py3.online/hmr/llms.txt "llms.txt — LLM-friendly docs") / [Docs MCP Server](https://py3.online/hmr/mcp "HMR MCP Server — py3.online/hmr/mcp")
- PyPI: [hmr](https://pypi.org/project/hmr/ "hmr - PyPI") / [hmr-daemon](https://pypi.org/project/hmr-daemon/ "hmr-daemon - PyPI") / [uvicorn-hmr](https://pypi.org/project/uvicorn-hmr/ "uvicorn-hmr - PyPI") / [mcp-hmr](https://pypi.org/project/mcp-hmr/ "mcp-hmr - PyPI")

## Comparisons

A good way to understand a project is through comparisons with similar projects to learn what it's "not". Since this project has both a reactive engine and hot-reload tool, the comparisons below will be made separately with related projects.

### Hot-reload Tools

[tach](https://github.com/gauge-sh/tach "A Python tool to enforce dependencies and interfaces, written in Rust") is relatively modern and attempts to build real dependency relationships, except for this project:

- not only track Python modules but also track any [opened](https://docs.python.org/3/library/functions.html#open "open() — Built-in function documentation") files during runtime
- has more Python ecosystem integration that works out-of-the-box, while tach only has a single [blog post](https://www.gauge.sh/blog/how-to-build-hot-module-replacement-in-python "How to build Hot Module Replacement in Python — Gauge blog") example for implementing hot reloading.

The other tools **do not track transitive dependencies** (they only reload directly modified code, which leaves dependent code out of sync), — maybe useful in tiny demos but fragile for real projects. Additionally, many of them rely on hacky monkey patching.

!!! note ""

    In contrast, **this project avoids monkey-patching**: it leverages Python's [`sys.meta_path`](https://docs.python.org/3/reference/import.html#the-meta-path "The import system — sys.meta_path (Python docs)") hooks and a custom [`ModuleType`](https://docs.python.org/3/library/types.html#types.ModuleType "types.ModuleType — Python docs") to track dependencies.

Besides these two aspects, here are some individual comparisons:

- Unlike [python-hmr](https://github.com/Mr-Milk/python-hmr "python-hmr — GitHub: Hot module reload for python"), this project supports reloading any variable within a module (not just classes and functions)
- [jurigged](https://github.com/breuleux/jurigged "jurigged — GitHub: Hot reloading for Python") implements loop statement reloading in a very magical way, but that's a rare use case, and in these cases you can move the loop code to a module and install `hmr-daemon` to achieve the same result
- [`#!py importlib.reload`](https://docs.python.org/3/library/importlib.html#importlib.reload "importlib.reload() — Python docs") in Python module mechanism, [`%autoreload`](https://ipython.readthedocs.io/en/stable/config/extensions/autoreload.html "IPython autoreload — extension documentation") in IPython and [reloadium](https://pypi.org/project/reloadium/ "reloadium - PyPI") etc. all apply to the two points above
- Cold-reload CLI tools like [watchfiles](https://github.com/samuelcolvin/watchfiles "watchfiles — GitHub: Simple, modern and fast file watching and code reload for Python, written in Rust") and [watchdog](https://github.com/gorakhargosh/watchdog "watchdog — GitHub: Python library and shell utilities to monitor filesystem events") purely restart the entire process when encountering changes, their compatibility is similar to HMR but sacrifices performance (the projects above sacrifice compatibility for performance, while this project has both). _The difference between hot and cold reload is similar to the difference between [bun](https://bun.com/docs/runtime/watch-mode "Watch Mode — Bun")'s `--hot` and `--watch`_

!!! success "Performance"

    Because of the technology choices it made, HMR's flexibility typically does **NOT** lead to higher overhead; rather, it becomes the best of both worlds.

### Reactive Programming Engines

- TUI library [textual](https://github.com/textualize/textual/ "Textual — terminal UI framework for Python"), Web app framework [py-shiny](https://github.com/posit-dev/py-shiny "Shiny for Python — posit-dev/py-shiny") both come with simple, framework-specific reactive systems[^3][^4], but their reactions are all async. HMR natively supports **both sync and async reactivity**. It is **framework-agnostic** and can be easily integrated with different UI frameworks
- The interactive notebook [marimo](https://github.com/marimo-team/marimo "marimo — Reactive notebook for Python") and the jupyter kernel [ipyflow](https://github.com/ipyflow/ipyflow "IPyflow — reactive kernel for Jupyter") both use static analysis for reactivity, which has the same disadvantages mentioned above for `tach`
- [RxPY](https://github.com/ReactiveX/RxPY "RxPY — ReactiveX for Python") is closer to an opinionated event-driven framework rather than a fine-grained reactivity engine _(despite "reactive" being in its name)_

HMR also supports **isolated reactivity Contexts**, which enables separated reactivity and a higher level of flexibility (docs still under construction), allowing developers to build their own powerful tools/frameworks on top of this engine without interfering with one another

### Comparable JavaScript Projects

- HMR aims to reach the usability and flexibility of libraries like [alien-signals](https://github.com/stackblitz/alien-signals "alien-signals — light signal library").
- The reactivity models of [Svelte](https://svelte.dev/docs/svelte/what-are-runes "What are runes? — Svelte docs"), [SolidJS](https://docs.solidjs.com/concepts/intro-to-reactivity "Intro to reactivity — Solid docs"), [Vue](https://vuejs.org/guide/extras/reactivity-in-depth "Reactivity in Depth — Vue.js Guide") gave this project inspiration and some reference
- One of the initial objectives of this project is to create [Vite](https://vite.dev/ "Vite — Next generation frontend tooling") and [Vitest](https://vitest.dev/ "Vitest — Vite-native testing framework") in the Python ecosystem.

!!! tip "Thanks"

    This project stands on the shoulders of earlier ones, inspired by their ideas and design choices.

Some of the wording here may sound exaggerated, but they are all facts. If you find exceptions, feel free to open an [Issue](https://github.com/promplate/hmr/issues "promplate/hmr issues — GitHub")

> This project also welcomes anyone to discuss future features!

[^1]: There is an article about reactivity concepts _(in JavaScript)_ at [dev.to](https://dev.to/dmnchzl/wtf-is-reactivity--4c1h "Damien Chazoule: WTF Is Reactivity !?")

[^2]: Another article: [Wikipedia](https://en.wikipedia.org/wiki/Reactive_programming#Change_propagation_algorithms "Reactive programming: Change propagation algorithms")

[^3]: Textual Docs: [textual.textualize.io](https://textual.textualize.io/guide/reactivity/ "Textual: Reactivity")

[^4]: Shiny for Python Docs: [shiny.posit.co](https://shiny.posit.co/py/docs/reactive-patterns.html "Shiny for Python: Reactive Patterns")
