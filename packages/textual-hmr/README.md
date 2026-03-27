# textual-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/textual-hmr)](https://pypi.org/project/textual-hmr/)

Provides [Hot Module Reloading](https://pyth-on-line.promplate.dev/hmr) for [Textual](https://textual.textualize.io/) apps.

It acts as a small runner for Textual development: save Python files, and the current app instance exits so the updated app can be started again in the same Python process. When you export an `App` subclass, `textual-hmr` also enables Textual's built-in CSS watching by default.

## CLI Usage

If your app class is named `DemoApp` in `./main.py`, run:

```sh
textual-hmr ./main.py:DemoApp
```

Or use a module import path:

```sh
textual-hmr my_package.main:DemoApp
```

The target may be:

- an `App` subclass
- an `App` instance
- a zero-argument callable returning an `App`

## Options

```sh
textual-hmr --help
```

The command supports:

- `--watch PATH`: additional file or directory paths to watch
- `--ignore PATH`: file or directory paths to ignore
- `--headless`: run without terminal output
- `--inline`: use Textual inline mode
- `--inline-no-clear`: preserve inline output after exit
- `--no-mouse`: disable mouse support
- `--clear`: clear the terminal before restarting after changes
- `--no-watch-css`: do not enable Textual's CSS watcher when the target is an `App` subclass

## Notes

- This package is currently optimized for the common dev loop of restarting a Textual app quickly in-process when code changes.
- It does not attempt to preserve widget state across app restarts.
- For CSS-only changes, Textual's own watcher is usually the smoother path; `textual-hmr` enables that automatically for exported app classes unless you disable it.
