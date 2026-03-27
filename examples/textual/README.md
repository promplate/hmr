# Textual Example

This example demonstrates how to use `textual-hmr` with a minimal Textual app.

## How to Run

After installing dependencies, run the following command in this directory:

```sh
textual-hmr main:DemoApp
```

## What to Observe

The app renders a short message from `message.py`.

- Change the string in `message.py` and save it.
- The current Textual app instance will exit and restart in-process.
- The updated message will appear immediately after restart.

Because `DemoApp` is exported as an `App` subclass, `textual-hmr` also enables Textual's built-in CSS watching by default. You can tweak the inline CSS in `main.py` to compare Python-level restarts with CSS-only refreshes.
