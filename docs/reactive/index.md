---
icon: lucide/power
---

# Reactive Programming Basics

_A little backstory: **reactive programming** is an evolution of the **observer pattern**._

The [observer pattern](https://en.wikipedia.org/wiki/Observer_pattern "Observer pattern - Wikipedia") provides an intuitive [event-driven programming](https://en.wikipedia.org/wiki/Event-driven_programming "Event-driven programming - Wikipedia") paradigm, treating **data** _(subjects)_ and **observers** as fundamental objects. When data changes, observers are notified and update accordingly.

But typical observer patterns require **manually binding dependency relationships between subjects and observers**. Reactive programming automates dependency tracking.

??? info "How this works?"

    A helpful starting point is the [call stack](https://docs.python.org/3/library/inspect.html#inspect.stack "inspect.stack - Python docs"): if A calls B, then A depends on B. In synchronous code, the current stack is often enough to explain what gets tracked.

    But HMR's implementation is not limited to plain stack inspection. In asynchronous code it carries the reactive execution context forward as coroutines run, so dependency tracking can continue across `await` boundaries too.

Once dependencies are tracked, "reactive" means automatic reactions to data changes. If **A** depends on B and C, then **A** reacts to B and C; B and C are **A**'s dependencies. That's reactive programming in essence.

!!! note ""

    Since live editing is invaluable in frontend development, reactive programming has flourished in languages like JavaScript. HMR (this project) brings cutting-edge reactive programming to Python, aiming to be the best-maintained choice balancing flexibility, performance, and accessibility.

## Signals and Effects

In HMR, there are two core primitives: [Signal](signals.md) and [Effect](effects.md). A Signal acts as a data source:

```python
from reactivity import signal

s = signal(0)  # initial value is 0

print(s.get())  # use .get() to read the signal

s.set(1)  # update its value to 1

print(s.get())
```

This prints 0 then 1. Without Effects, Signals are just data containers.

Effects subscribe to data sources and rerun when they change:

```python
from reactivity import signal, effect

s = signal(0)

effect(lambda: print(s.get()))  # prints 0 initially
```

Unlike a simple `#!py print(s.get())`, when `s` changes, related Effects automatically rerun. For example:

```python
s.set(1)
```

This prints 1, even without explicitly calling `#!py print(s.get())` again.

!!! note ""

    In the examples above, we used lambdas as handlers. But you can pass any callable! I personally prefer the decorator style:

    ```python
    @effect
    def _():
        print(s.get())
    ```

## Idempotent Computations

HMR provides another primitive: [Derived](derived.md), representing data processing pipelines. Values are cached if dependencies haven't changed. Computation is lazy, unlike [Effects](#signals-and-effects), which are uncached and should not be used for returning values. Effects are about "what to do with data" while Derived is about "returning processed data".

```python
from reactivity import signal, derived

s = signal(0)

@derived
def f():
    print("expensive computing ...")
    return s.get() * 2
```

Unlike Effects that run immediately, nothing happens until you call `#!py f()`:

```python
print(f())
# expensive computing ...
# 0
print(f())
# 0
```

Multiple calls return cached values without recomputing, since idempotent pipelines should return the same result when inputs are unchanged.

But after `#!py s.set(...)`, calling `#!py f()` again triggers recomputation:

```python
s.set(1)
print(f())
# expensive computing ...
# 2
print(f())
# 2
```

## Dependency Graph

Signals, Derived, and Effects form the complete [reactive primitives](advanced.md). Imagine a graph: leftmost nodes are pure data sources (inputs, files, time), depending on nothing. Rightmost nodes are Effects, depended by nothing. Middle nodes are intermediate computations, depending on left nodes and depended by right nodes.

```python
from reactivity import signal, derived, effect

s = signal(0)

@derived
def f():
    return s.get() // 2

@effect
def _():
    print(f())

# output: 0

s.set(1)
# no output because f() = 0, unchanged, no need to rerun effect

s.set(2)
# output: 1

s.set(3)
# no output
```

HMR's reactivity engine handles all this behind the scenes: ensuring idempotent operations don't rerun unnecessarily, while guaranteeing data changes are reflected without being lost.

## Async Variants

The same model extends to async code.

- `async_effect` is the async counterpart of `effect`
- `async_derived` is the async counterpart of `derived`
- `async_derived` is still cached and lazy, but you read it with `await value()`
- Async primitives should be created inside an active async runtime, or with an explicit `task_factory`

For the full semantics and runnable examples, see [Async Reactive Primitives](../references/reactivity/async_primitives.md).

---

Let's take a step further. Obviously, any script can be viewed this way. Local data analysis typically follows:

1. Load data from files
2. Process and compute statistics
3. Present results in some form

## Towards Hot Reloading

Even if the reactive paradigm doesn't appeal to you (I'd love to hear why in the repo discussions!), we offer a non-intrusive way to enhance development experience: the `hmr` CLI.

!!! note ""

    As mentioned, any script can be seen as a reactive graph, even without explicit usage. The HMR CLI embodies this philosophy. It provides a drop-in replacement for the Python CLI:

    1. HMR **treats each file as a data source** (signal), whether Python modules or files opened via `#!py open` / `pathlib`
    2. Imported **variables are derived values**
    3. The **entry file** you run with `python foo.py` or `python -m foo.bar` is an Effect, since nothing depends on it

When files change (code edits or data updates), directly affected Python modules rerun. Updated variables trigger dependent modules to rerun, propagating upward until the entry, or stopping earlier when nothing observable changed. You know, not every butterfly wing flap causes a hurricane.

Simply use `hmr ...` instead of `python ...` to run your code, and see results update instantly when saving files—saving hundreds of times the development time! Compared to your changes, `python ...` cold starts waste time on unchanged parts.

!!! note ""

    Although I haven't formally measured it, unless you're developing heavy libraries, your code compilation time is typically tiny compared with third-party imports and Python bootstrap time—this should be Python development common sense.

Thanks to meticulous engineering and perfectionism, HMR brings instant feedback to every Python project.

!!! note ""

    While HMR's original goal was bringing [Vite](https://github.com/vitejs/vite "vitejs/vite - GitHub")/[Vitest](https://github.com/vitest-dev/vitest "vitest-dev/vitest - GitHub")-like experiences from JavaScript to Python, it does more. As a more flexible language (trading some performance), Python makes **finer-grained runtime dependency tracking** possible _(via module-level `__getattr__`/`__setattr__`, async context isolation, etc.)_. JavaScript developers, come and try Python!

???+ info "Note"

    While the previous section briefly touched on hot reloading implementation, this represents just one application of the reactive programming framework. Hot reloading is an optional feature that can be easily implemented with the reactivity engine, but it's not the sole purpose or requirement of this library. The reactive programming paradigm offers much broader potential beyond just development tooling, enabling sophisticated data flow management, automatic state synchronization, and interactive applications across various domains.

## What's Next

For optional configurations of reactive primitives, reaction batching, async reactivity, etc., navigate to [signals](signals.md), [derived](derived.md), [effects](effects.md), [advanced](advanced.md), and [Async Reactive Primitives](../references/reactivity/async_primitives.md).
