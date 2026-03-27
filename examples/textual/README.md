# Textual Example

This example demonstrates how to use `textual-hmr` with a minimal Textual app.

## How to Run

After installing dependencies, run the following command in this directory:

```sh
textual-hmr main:DemoApp
```

## What to Observe

The app renders a short message from `message.py` and loads styles from `main.tcss`.

- Change the string in `message.py` and save it.
- The current Textual app instance will exit and restart in-process.
- The updated message will appear immediately after restart.
- Change `main.tcss` and save it.
- Textual will hot-refresh the stylesheet without restarting the Python app.

Because `DemoApp` is exported as an `App` subclass, `textual-hmr` also enables Textual's built-in CSS watching by default. This example lets you compare Python-level restarts with TCSS-only refreshes side by side.
