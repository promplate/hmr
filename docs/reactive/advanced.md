# Advanced Reactivity

Master advanced reactive programming patterns and optimization techniques.

## Push-Pull Reactivity

HMR implements push-pull reactivity, combining the best of both worlds:

**Push Style (Eager):** Subscribables notify subscribers immediately. Can cause unnecessary recalculations. Low latency for immediate reactions.

**Pull Style (Lazy):** Computations pull values when needed. Avoids unnecessary recalculations. Higher latency for initial results.

**Push-Pull Hybrid:** Subscribables push notifications (eager). Computations defer unless pulled by effects (lazy). Best of both: no waste + low latency.

HMR uses this approach automatically.

## Batch Updates

Combine multiple updates into a single notification:

```python
from reactivity import signal, effect, batch

count = signal(0)
name = signal("test")

effect(lambda: print(f"{name.get()}: {count.get()}"))

with batch():
    count.set(2)
    name.set("batch")
```

## Memoization

Cache computed results to avoid recalculation:

```python
from reactivity import memoized

@memoized
def expensive_operation():
    print("Computing...")
    return sum(range(1_000_000))

result = expensive_operation()
```

## Dynamic Dependencies

Dependencies can change based on runtime conditions:

```python
from reactivity import signal, derived

mode = signal("sum")
values = signal([1, 2, 3])

result = derived(lambda: (
    sum(values.get()) if mode.get() == "sum"
    else max(values.get()) if mode.get() == "max"
    else len(values.get())
))

mode.set("max")
```

## Context Isolation

Use contexts to manage reactive scope:

```python
from reactivity import new_context, signal, effect

ctx = new_context()
count = ctx.signal(0)
ctx.effect(lambda: print(count.get()))
```

Use cases: Testing reactive code, multiple independent reactive systems, module isolation in HMR.

## Best Practices

### 1. Keep Derived Values Pure

Good - no side effects:

```python
squared = derived(lambda: count.get() ** 2)
```

Use effect for side effects instead:

```python
effect(lambda: print(f"Count: {count.get()}"))
```

### 2. Minimize Effect Scope

Good - focused effect:

```python
full_name = derived(lambda: f"{first_name.get()} {last_name.get()}")
effect(lambda: print(full_name.get()))
```

### 3. Use Batch for Related Updates

Good - batch updates:

```python
with batch():
    form_data.set({**form_data.get(), "name": "John"})
    form_data.set({**form_data.get(), "email": "john@example.com"})
```

### 4. Prefer Immutability

Good - signals with immutable values:

```python
items = signal([])
items.set([*items.get(), "new_item"])
```

### 5. Clean Up Resources

```python
effect_ref = effect(lambda: setup_listener())
effect_ref.dispose()
```

## Next Steps

- Review [Performance Tips](../getting-started/quick-start.md)
- Explore [HMR Integrations](../integrations/demo.md)
- Try [MCP Integration](../integrations/mcp.md) with AI

Learn more: [Signals](./signals.md) | [Effects](./effects.md) | [Derived Values](./derived.md)
