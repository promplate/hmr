# Signals

Signals are the foundation of reactive programming in HMR. They represent observable values that notify subscribers when they change.

## What is a Signal?

A signal is a container for a value that:

- Holds a current value
- Notifies dependents when the value changes
- Can be read and written
- Tracks what code depends on it

## Creating Signals

```python
from reactivity import signal

# Create a signal with an initial value
count = signal(0)

# Create a signal with no initial value
data = signal()

# Create a signal that doesn't check equality
raw_signal = signal([], check_equality=False)
```

## Reading Values

```python
# Inside an effect, derived, or during tracking
value = count.get()

# Shorthand (when used in reactive context)
value = count.get(track=True)

# Don't track dependencies
value = count.get(track=False)
```

## Writing Values

```python
# Set a new value
count.set(5)

# Update based on current value
count.update(lambda x: x + 1)
```

## Reactivity

When you read a signal inside a reactive context (effect, derived), you automatically track that dependency:

```python
from reactivity import signal, effect

count = signal(0)

# This effect subscribes to `count`
effect(lambda: print(f"Count is {count.get()}"))

count.set(1)  # Prints: "Count is 1"
```

## Equality Checking

By default, signals check if the new value equals the old value:

```python
count = signal(0, check_equality=True)
count.set(0)  # No notification, value didn't change

# Disable equality checking
raw = signal([], check_equality=False)
raw.set([])  # Notifies even though value is equivalent
```

## Use Cases

- **State management**: Application state that changes over time
- **Configuration**: Settings that can be updated
- **Counters**: Track numeric values
- **Flags**: Boolean state like loading, error status
- **Form inputs**: User input values

## Next Steps

- Learn about [Effects](./effects.md) to react to signal changes
- Create [Derived values](./derived.md) from signals
- Explore [Advanced patterns](./advanced.md)

Learn more: [Effects](./effects.md) | [Derived Values](./derived.md) | [Advanced Reactivity](./advanced.md)
