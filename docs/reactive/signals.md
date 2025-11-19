# Signals

Observable values with automatic dependency tracking. Signals are the foundation of reactive programming, evolving from the [observer pattern](https://en.wikipedia.org/wiki/Observer_pattern "Observer pattern — Wikipedia") where data sources notify observers of changes.

> The traditional observer pattern requires manual binding of subject and observer **dependencies**. Reactive programming implements automatic tracking of these dependencies on top of that.
>
> The principle of automatic tracking is simple -- for example, the call stack actually records dependencies between calls. If we consider A calls B as representing A depends on B, then through the relative positions of A and B on the call stack, we can determine the dependency relationship between A and B.

```python
from reactivity import signal, state
```

[`signal`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py "hmr reactivity: _curried.py — GitHub") creates standalone observables. [`state`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py "hmr reactivity: _curried.py — GitHub") is a descriptor for class attributes.

## Usage

In HMR, there are two primitives: Signal and Effect. Signal is like a data source:

```python
from reactivity import signal

s = signal(0)  # initial value is 0

print(s.get())  # use .get() to get the value of a signal

s.set(1)  # set its value to 1

print(s.get())
```

This will print out 0 and then 1. As you can see, Signal is just a data source that tracks changes.

## State descriptor

For class attributes, you can use the `state` descriptor:

```python
class Counter:
    value = state(0)

c = Counter()
c.value = 1       # notifies effects reading Counter.value
```

## Tips

- `get(track=False)` reads without subscribing to changes
- [`batch()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py "hmr reactivity: _curried.py — GitHub") groups updates to reduce recomputations
- Use `state` for instance attributes, `signal` for standalone values
- Signals automatically track dependencies in [call stacks](https://docs.python.org/3/library/inspect.html#inspect.stack "inspect.stack — Python docs"), eliminating manual observer binding

## See Also

- [Effects](effects.md "Effects") for side effects that react to signals
- [Derived](derived.md "Derived") for computed values based on signals
- [Advanced Reactivity](advanced.md "Advanced Reactivity") for patterns and best practices
