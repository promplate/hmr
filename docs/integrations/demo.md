# Demo Project

The demo project in `examples/demo/` showcases HMR capabilities with a minimal, pure Python setup.

## Project Structure

```text
examples/demo/
├── entry.py       # Entry point
├── a.py           # Module A
├── b.py           # Module B
└── common.py      # Shared utilities
```

## Running the Demo

```sh
cd examples/demo
pip install -e .
hmr entry.py
```

## What It Does

The demo demonstrates:

- **Module reloading**: Edit `a.py` or `b.py` and see changes reflected instantly
- **State preservation**: Global state survives reloads
- **Dependency tracking**: Only affected modules rerun

## Try It Out

Edit `a.py` or `b.py` while the demo is running. You'll see:

```text
Hot reload triggered for a.py
Hot reload triggered for b.py (since it depends on a.py)
Application continues running with new code
```

## Key Takeaway

This demo shows that HMR works without special frameworks. It's built into the `hmr` CLI and applies to any Python code.

Learn more: [CLI Reference](../getting-started/cli.md) | [Next: Flask Integration](./flask.md)
