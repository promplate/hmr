# Effects

Effects are functions that run automatically whenever their dependencies change. They form the "reactive" part of reactive programming.

## What is an Effect?

An effect is:

- A function that observes dependencies (signals, derived values)
- Automatically triggered when dependencies change
- Useful for side effects: logging, network requests, UI updates, etc.

## Creating Effects

```python
from reactivity import effect, signal

# Create an effect that runs immediately
effect(lambda: print("This runs now"))

# Create an effect that only runs on dependency changes
effect(lambda: print("Dependency changed"), call_immediately=False)

# Using decorator syntax
@effect
def my_effect():
    print("Running effect")

# Decorator without immediate call
@effect(call_immediately=False)
def delayed_effect():
    print("Running when dependency changes")
```

## Tracking Dependencies

When you read a signal inside an effect, it automatically tracks that dependency:

```python
from reactivity import effect, signal

count = signal(0)

# This effect depends on `count`
effect(lambda: print(f"Count: {count.get()}"))

count.set(1)  # Triggers effect, prints: "Count: 1"
count.set(2)  # Triggers effect, prints: "Count: 2"
```

## Multiple Dependencies

Effects track all signals read during their execution:

```python
count = signal(0)
name = signal("Alice")

effect(lambda: print(f"{name.get()}: {count.get()}"))
# Output: "Alice: 0"

count.set(1)  # Triggers effect
# Output: "Alice: 1"

name.set("Bob")  # Also triggers effect
# Output: "Bob: 1"
```

## Side Effects

Effects are perfect for side effects:

```python
from reactivity import effect, signal

# Logging
status = signal("idle")
effect(lambda: print(f"Status changed: {status.get()}"))

# Fetching data
user_id = signal(1)
effect(lambda: fetch_user(user_id.get()))

# Updating the DOM (if using web framework)
theme = signal("light")
effect(lambda: document.body.className = f"theme-{theme.get()}")
```

## Disposal

Effects can be disposed to stop tracking:

```python
my_effect = effect(lambda: print("Running"))
my_effect.dispose()  # Effect no longer triggers on dependency changes
```

## Use Cases

- **Logging**: Log whenever state changes
- **Network requests**: Fetch data when dependencies change
- **UI updates**: Sync reactive state with DOM
- **Persistence**: Save state to localStorage
- **Validation**: Validate when input changes
- **Debugging**: Print state for development

## Common Pattern: Component State

```python
from reactivity import signal, effect

class Component:
    def __init__(self):
        self.data = signal(None)
        self.loading = signal(False)
        self.error = signal(None)
        
        # Set up effects
        effect(self.load_data)
        effect(self.on_error)
    
    def load_data(self):
        self.loading.set(True)
        try:
            self.data.set(fetch_from_api())
        except Exception as e:
            self.error.set(str(e))
        finally:
            self.loading.set(False)
    
    def on_error(self):
        if self.error.get():
            print(f"Error: {self.error.get()}")
```

## Async Effects

For asynchronous operations:

```python
from reactivity import async_effect

# Automatically handles async code
async_effect(async lambda: await fetch_data())
```

See [Advanced Reactivity](./advanced.md) for more on async patterns.

## Next Steps

- Combine with [Derived Values](./derived.md)
- Learn [Advanced patterns](./advanced.md)
- Explore [Best practices](./advanced.md#best-practices)

Learn more: [Signals](./signals.md) | [Derived Values](./derived.md) | [Advanced Reactivity](./advanced.md)
