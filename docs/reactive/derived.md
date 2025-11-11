# Derived Values

Derived values are computed properties that automatically update when their dependencies change. They combine reactivity with memoization.

## What is a Derived Value?

A derived value:

- Computes a value based on dependencies
- Updates automatically when dependencies change
- Caches the result (doesn't recompute if dependencies didn't change)
- Is itself a subscribable value for other computations

## Creating Derived Values

```python
from reactivity import derived, signal

count = signal(5)

# Create a derived value
squared = derived(lambda: count.get() ** 2)

# Reading a derived value
result = squared()  # Returns 25
```

## Basic Example

```python
from reactivity import derived, signal, effect

first_name = signal("John")
last_name = signal("Doe")

# Derived value that combines two signals
full_name = derived(lambda: f"{first_name.get()} {last_name.get()}")

effect(lambda: print(full_name()))
# Output: "John Doe"

first_name.set("Jane")
# Output: "Jane Doe"
```

## Memoization

Derived values are memoized—they only recompute when dependencies actually change:

```python
count = signal(0)

# This is expensive
computed = derived(lambda: expensive_calculation(count.get()))

computed()  # Recomputes
computed()  # Returns cached result (no recomputation)

count.set(1)  # Dependency changed
computed()  # Recomputes again
```

## Chaining Derived Values

Derived values can depend on other derived values:

```python
count = signal(0)

doubled = derived(lambda: count.get() * 2)
quadrupled = derived(lambda: doubled() * 2)

effect(lambda: print(quadrupled()))
# Output: 0

count.set(5)
# Output: 40  (5 * 2 * 2)
```

## Performance Benefits

Derived values prevent unnecessary recalculations:

```python
# Without derived (recalculates every time)
data = [expensive_transform(item) for item in signal_list.get()]

# With derived (caches result)
transformed_data = derived(
    lambda: [expensive_transform(item) for item in signal_list.get()]
)

# Only recomputes when signal_list changes
result = transformed_data()
result = transformed_data()  # No recomputation
```

## Equality Checking

Like signals, derived values check equality:

```python
# Default: check equality
computed = derived(lambda: [1, 2, 3])

# Subscribers only notified if result changes
# [1, 2, 3] != [1, 2, 3, 4], so notification happens

# Disable equality checking
computed = derived(
    lambda: [1, 2, 3],
    check_equality=False
)
# Subscribers notified even if equivalent
```

## Use Cases

- **Computed properties**: Derive values from state
- **Aggregation**: Sum, count, or aggregate signal values
- **Transformation**: Transform state into display format
- **Filtering**: Filter list signals
- **Caching**: Avoid expensive recalculations
- **Formatting**: Format values for display

## Practical Example: Shopping Cart

```python
from reactivity import signal, derived, effect

items = signal([
    {"name": "Apple", "price": 1.50, "qty": 3},
    {"name": "Banana", "price": 0.50, "qty": 2},
])

# Derived: total price per item
def item_totals():
    return derived(lambda: [
        item["price"] * item["qty"]
        for item in items.get()
    ])

# Derived: cart total
cart_total = derived(
    lambda: sum(item["price"] * item["qty"] for item in items.get())
)

effect(lambda: print(f"Total: ${cart_total():.2f}"))
# Output: "Total: $5.50"

items.set([...items.get(), {"name": "Orange", "price": 0.75, "qty": 4}])
# Output: "Total: $8.50"
```

## Async Derived Values

For asynchronous computations:

```python
from reactivity import async_derived

user_id = signal(1)

# Fetch user data whenever user_id changes
user_data = async_derived(
    lambda: fetch_user(user_id.get())
)

# Access the result
print(await user_data())
```

## Next Steps

- Combine with [Effects](./effects.md)
- Learn [Advanced patterns](./advanced.md)
- See [Best practices](./advanced.md#best-practices)

Learn more: [Signals](./signals.md) | [Effects](./effects.md) | [Advanced Reactivity](./advanced.md)
