# Quick Start

Let's create a simple example to see HMR in action.

## 1. Create a file

Create `main.py`:

```python
# main.py
import time

i = 0
while True:
    i += 1
    print(f"Hello, World! {i}")
    time.sleep(1)
```

## 2. Run with HMR

```sh
hmr main.py
```

You should see:

```text
Hello, World! 1
Hello, World! 2
Hello, World! 3
...
```

## 3. Edit and watch it reload

Without stopping the script, change the print statement:

```python
# main.py
import time

i = 0
while True:
    i += 1
    print(f"HMR is amazing! {i}")  # Changed this line
    time.sleep(1)
```

Save the file. The output changes instantly:

```text
...
Hello, World! 5
HMR is amazing! 6
HMR is amazing! 7
...
```

Notice that `i` continued from where it was. The process didn't restart—only the changed code was reloaded. This is the power of HMR: instant updates with preserved state.

## Framework Examples

**FastAPI/ASGI apps:**

```sh
pip install uvicorn-hmr
uvicorn-hmr main:app
```

**Flask apps:**

```sh
hmr start.py  # where start.py creates and runs your Flask app
```

**MCP servers:**

```sh
pip install mcp-hmr
mcp-hmr server:app
```

## Next Steps

- Explore [reactive primitives](../reactive/signals.md "Signals") for building reactive applications
- Read [integration guides](../integrations/uvicorn.md "ASGI — uvicorn-hmr") for framework-specific patterns
- Check [advanced patterns](../reactive/advanced.md "Advanced Reactivity") for best practices
