# Introduction

[HMR](https://pypi.org/project/hmr/) provides a pythonic, flexible, progressive-yet-intuitive reactive programming **engine**/**framework**, and on top of that, a fine-grained, on-demand hot-reload **tool**.

- [x] The reactive engine implements high-performance **push-pull reactivity**, letting you elegantly do reactive programming in Python _(read more about push-pull reactivity: [`1`](https://dev.to/dmnchzl/wtf-is-reactivity--4c1h) [`2`](https://en.wikipedia.org/wiki/Reactive_programming#Change_propagation_algorithms))_
- [x] The hot-reloading tools are **drop-in replacements** for `python` CLI, `uvicorn`, and `mcp / fastmcp` integration, boosting your development with or without frameworks

Most features are usable independently. **Any** user can use one or more of the features above to improve development efficiency _(not exaggerating, this library improved my debugging efficiency by 10x-100x)_

This project supports Python 3.12+, and is compatible with alternative Python runtimes like [Pyodide](https://github.com/pyodide/pyodide), [GraalPy](https://github.com/oracle/graalpython), and [RustPython](https://github.com/RustPython/RustPython). It supports both asyncio and trio, and is well-tested in 3.12-3.14.

> The project requires at least Python 3.12 because the I values static typing, and 3.12 started supporting some convenient new features in static typing. If you don't mind these, you can easily support down to 3.10 or even 3.8 with minor changes.

## Links

- Repo: Most code lives in [promplate/hmr](https://github.com/promplate/hmr), except the [core library](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr) and [tests](https://github.com/promplate/pyth-on-line/tree/main/tests/py) are temporarily in another repo
- Docs: [Docs](https://hmr.promplate.dev/) / [Interactive Docs (under construction)](https://py3.online/hmr) / [llms.txt](https://py3.online/hmr/llms.txt) / [Docs MCP Server](https://py3.online/hmr/mcp)
- PyPI: [hmr](https://pypi.org/project/hmr/) / [hmr-daemon](https://pypi.org/project/hmr-daemon/) / [uvicorn-hmr](https://pypi.org/project/uvicorn-hmr/) / [mcp-hmr](https://pypi.org/project/mcp-hmr/)

## Comparisons

A good way to understand a project is through comparisons with similar projects to learn what it's "not". Since this project has both a reactive engine and hot-reload tool, the comparisons below will be made separately with related projects.

### Hot-reload Tools

[tach](https://github.com/gauge-sh/tach) is relatively modern and attempts to build real dependency relationships, except for this project:

- builds dependency graphs **at runtime**, thus supporting dependencies generated in various forms (e.g., `exec("import ...")`, `importlib.import_module(...)`), which cannot be statically analyzed and are widespread in the Python ecosystem
- builds **variable-level** fine-grained dependency graphs, while tach only supports coarse-grained module-module dependencies. tach's coarse-grained dependency graphs often invalidate all nodes for any change
- not only track Python modules but also track any [opened](https://docs.python.org/3/library/functions.html#open) files during runtime
- has more Python ecosystem integration that works out-of-the-box, while tach only has a single [blog post](https://www.gauge.sh/blog/how-to-build-hot-module-replacement-in-python) example for implementing hot reloading.

The other tools **do not track transitive dependencies** (they only reload directly modified code, which leaves dependent code out of sync), — maybe useful in tiny demos but fragile for real projects. Additionally, many of them rely on hacky monkey patching.

> In contrast, this project avoids monkey-patching: it leverages Python's `sys.meta_path` hooks and a custom module type to track dependencies.

Besides these two aspects, here are some individual comparisons:

- Unlike [python-hmr](https://github.com/Mr-Milk/python-hmr), this project supports reloading any variable within a module (not just classes and functions)
- [jurigged](https://github.com/breuleux/jurigged) implements loop statement reloading in a very magical way, but that's a rare use case, and in these cases you can move the loop code to a module and install `hmr-daemon` to achieve the same result
- [`importlib.reload`](https://docs.python.org/3/library/importlib.html#importlib.reload) in Python module mechanism, [`%autoreload`](https://ipython.readthedocs.io/en/stable/config/extensions/autoreload.html) in IPython and [reloadium](https://pypi.org/project/reloadium/) etc. all apply to the two points above
- Cold-reload CLI tools like [watchfiles](https://github.com/samuelcolvin/watchfiles) and [watchdog](https://github.com/gorakhargosh/watchdog) purely restart the entire process when encountering changes, their compatibility is similar to HMR but sacrifices performance (the projects above sacrifice compatibility for performance, while this project has both). _The difference between hot and cold reload is similar to the difference between [bun](https://bun.com/docs/runtime/watch-mode)'s `--hot` and `--watch`_

Because of the technology choices it made, HMR's flexibility typically does **NOT** lead to higher overhead; rather, it becomes the best of both worlds.

### Reactive Programming Engines

- TUI library [textual](https://github.com/textualize/textual/), Web app framework [py-shiny](https://github.com/posit-dev/py-shiny) both come with simple, framework-specific reactive systems [`1`](https://textual.textualize.io/guide/reactivity/) [`2`](https://shiny.posit.co/py/docs/reactive-patterns.html), but their reactions are all async. HMR natively supports **both sync and async reactivity**. It is **framework-agnostic** and can be easily integrated with different UI frameworks
- The interactive notebook [marimo](https://github.com/marimo-team/marimo) and the jupyter kernel [ipyflow](https://github.com/ipyflow/ipyflow) both use static analysis for reactivity, which has the same disadvantages mentioned above for `tach`
- [RxPY](https://github.com/ReactiveX/RxPY) is closer to an opinionated event-driven framework rather than a fine-grained reactivity engine _(despite "reactive" being in its name)_

HMR also supports **isolated reactivity Contexts**, which enables separated reactivity and a higher level of flexibility (docs still under construction), allowing developers to build their own powerful tools/frameworks on top of this engine without interfering with one another

### Comparable JavaScript Projects

- HMR aims to reach the usability and flexibility of libraries like [alien-signals](https://github.com/stackblitz/alien-signals).
- The reactivity models of [Svelte](https://svelte.dev/docs/svelte/what-are-runes), [SolidJS](https://docs.solidjs.com/concepts/intro-to-reactivity), [Vue](https://vuejs.org/guide/extras/reactivity-in-depth) gave this project inspiration and some reference
- One of the initial objectives of this project is to create [Vite](https://vite.dev/) and [Vitest](https://vitest.dev/) in the Python ecosystem.

> HMR has benefited greatly from the ideas and inspiration provided by these earlier projects.

Some of the wording here may sound exaggerated, but they are all facts. If you find exceptions, feel free to open an [Issue](https://github.com/promplate/hmr/issues)

> This project also welcomes anyone to discuss future features!
