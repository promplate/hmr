# Reactive Programming Basics

Reactive programming is an evolution of the observer pattern.

-> The [observer pattern](https://en.wikipedia.org/wiki/Observer_pattern "Observer pattern — Wikipedia") provides an intuitive [event-driven programming](https://en.wikipedia.org/wiki/Event-driven_programming "Event-driven programming — Wikipedia") paradigm, treating data and observers as fundamental objects. When data changes, observers are notified and update accordingly.

Traditional observer patterns require manually binding dependencies between subjects and observers. Reactive programming automates this dependency tracking.

-> The principle is simple: [call stacks](https://docs.python.org/3/library/inspect.html#inspect.stack "inspect.stack — Python docs") record dependencies between calls. If we consider A calling B as A depending on B, then the relative positions in the call stack reveal the dependency relationships.

Once dependencies are tracked, "reactive" means automatic reactions to data changes. If A depends on B and C, then A reacts to B and C; B and C are A's dependencies. That's reactive programming in essence.

> Since live editing is invaluable in frontend development, reactive programming has flourished in languages like JavaScript. HMR (this project) brings cutting-edge reactive programming to Python, aiming to be the best-maintained choice balancing flexibility, performance, and accessibility.

## Signals and Effects

In HMR, there are two core primitives: [Signal](signals.md "Signals") and Effect. A Signal acts as a data source:

Source: [hmr reactivity primitives — GitHub](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/primitives.py "hmr reactivity primitives — GitHub")

```python
from reactivity import signal

s = signal(0)  # initial value is 0

print(s.get())  # use .get() to read the signal

s.set(1)  # update its value to 1

print(s.get())
```

This prints 0 then 1. Signals are [observable data containers](https://en.wikipedia.org/wiki/ReactiveX "ReactiveX — Reactive programming & Observables (Wikipedia)").

[Effects](effects.md "Effects") subscribe to data sources and rerun when they change:

Source: [hmr reactivity primitives — GitHub](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/primitives.py "hmr reactivity primitives — GitHub")

```python
from reactivity import signal, effect

s = signal(0)

effect(lambda: print(s.get()))  # prints 0 initially
```

Unlike a simple `print(s.get())`, when `s` changes, related Effects automatically rerun. For example:

```python
s.set(1)
```

This prints 1, even without explicitly calling `print(s.get())` again.

> In the examples above, we used lambdas as handlers. But you can pass any callable! I personally prefer the decorator style:

```python
@effect
def _():
    print(s.get())
```

## Caching Idempotent Computations

HMR provides another primitive: [Derived](derived.md "Derived"), representing optimized data pipelines. Values are cached if dependencies haven't changed. Computation is lazy (unlike Effects, which are uncached and shouldn't return values—Effects are about "what to do with data" while Derived is about "returning processed data").

Source: [hmr reactivity primitives — GitHub](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/primitives.py "hmr reactivity primitives — GitHub")

```python
from reactivity import signal, derived

s = signal(0)

@derived
def f():
    print("expensive computing ...")
    return s.get() * 2
```

Unlike Effects that run immediately, nothing happens until you call `f()`:

```python
print(f())
# expensive computing ...
# 0
print(f())
# 0
```

Multiple calls return cached values without recomputing, since idempotent pipelines should return the same result when inputs are unchanged.

But after `s.set(...)`, calling `f()` again triggers recomputation:

```python
s.set(1)
print(f())
# expensive computing ...
# 2
print(f())
# 2
```

## Dependency Graph

Signals, Derived, and Effects form the complete [reactive primitives](advanced.md "Advanced Reactivity"). Imagine a graph (like neural network visualizations): leftmost nodes are pure data sources (inputs/files/time), depending on nothing. Rightmost are Effects, depending on nothing else. Middle nodes are intermediate computations, depending on left nodes and depended on by right nodes.

> Obviously, any script can be viewed this way. Local data analysis typically follows:

1. Load data from files
2. Process and compute statistics
3. Present results in some form

Source: [hmr reactivity primitives — GitHub](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/primitives.py "hmr reactivity primitives — GitHub")

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

## Hot Module Reload

Even if the reactive paradigm doesn't appeal to you (I'd love to hear why in the repo discussions!), we offer a non-intrusive way to enhance development experience: the [`hmr` CLI](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr-daemon "hmr-daemon — GitHub").

> As mentioned, any script can be seen as a reactive graph (even without explicit usage). The HMR CLI embodies this philosophy. It provides a drop-in replacement for the Python CLI:

1. HMR treats each file as a data source (signal)—whether Python modules or files opened via `open`/`pathlib`
2. Imported variables are derived values
3. The entry file you run with `python foo.py` or `python -m foo.bar` is an Effect, since nothing depends on it

When files change (code edits or data updates), directly affected Python code reruns ([module-by-module](https://docs.python.org/3/library/importlib.html "importlib — Python docs")). Updated variables trigger dependent modules to rerun, propagating upward until the entry (or stopping short, like not every butterfly wing flap causes a typhoon).

Simply use `hmr …` instead of `python …` to run your code, and see results update instantly when saving files—saving hundreds of times the development time! Compared to your changes, `python …` cold starts waste time on unchanged parts.

> While I haven't formally measured it, unless you're developing heavy libraries, your code compilation is typically less than 1‰ of third-party libraries and Python bootstrap time—this should be Python development common sense.

Thanks to meticulous engineering and perfectionism, HMR brings instant feedback to every Python project.

> While HMR's original goal was bringing Vite/Vitest-like experiences from JavaScript to Python, it does more. As a more flexible language (trading some performance), Python enables finer-grained runtime dependency tracking (module `__getattr__`/`__setattr__`, async context isolation, etc.). JavaScript developers, come experience Python!

## What's Next

For optional configurations of reactive primitives, reaction batching, async reactivity, etc., navigate to [signals](signals.md "Signals"), [derived](derived.md "Derived"), [effects](effects.md "Effects"), and [advanced](advanced.md "Advanced Reactivity") documentation pages.
