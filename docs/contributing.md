# Contributing

We welcome contributions! This document outlines the contribution workflow and code standards.

## Getting Started

### Fork and Clone

```sh
git clone https://github.com/YOUR_USERNAME/hmr.git
cd hmr
```

### Set Up Development Environment

```sh
python -m venv .venv
.venv/Scripts/activate
```

Or with uv:

```sh
uv sync
```

### Install in Development Mode

```sh
pip install -e ".[dev]"
```

## Development Workflow

### 1. Create a Feature Branch

```sh
git checkout -b feature/your-feature-name
```

Use descriptive branch names: `feature/`, `fix/`, `docs/`.

### 2. Make Your Changes

- Keep changes focused and atomic
- Follow the code style (see below)
- Add or update tests for new functionality
- Update documentation if needed

### 3. Run Tests Locally

```sh
pytest
```

Check linting:

```sh
ruff check .
ruff format .
```

### 4. Build and Test Docs Locally

```sh
zensical build
```

Preview the built site in `site/` directory.

### 5. Commit and Push

Write clear, concise commit messages:

```sh
git commit -m "add hot reload support for dataclasses"
git push origin feature/your-feature-name
```

### 6. Open a Pull Request

- Reference any related issues
- Provide a clear description of changes
- Ensure CI passes

## Code Standards

### Python Style

- Use Python 3.12+ features
- Follow PEP 8
- Use `pathlib` instead of `os.path`
- Lazy import non-stdlib modules
- Use type hints for parameters

### Documentation Style

- Docstrings for all public functions and classes
- Keep documentation clear and concise
- Link to related concepts

### Commit Message Format

Use lowercase with no period, under 80 characters:

- `add fine-grained reactivity tracking`
- `fix circular dependency detection`
- `update reactive programming guide`

## Testing

### Running Tests

```sh
pytest
pytest -v
pytest -s
```

### Writing Tests

- Tests go in `tests/` directory
- Test files start with `test_`
- Use descriptive test names

Example test:

```python
def test_signal_notifies_on_change():
    sig = signal(0)
    called = []
    effect(lambda: called.append(sig.get()))
    sig.set(1)
    assert called == [0, 1]
```

## Documentation

Docs are in `docs/` and built with Zensical.

### Doc Structure

- `docs/index.md` — Main landing page
- `docs/getting-started/` — Getting started guides
  - `intro.md` — Introduction to HMR
  - `installation.md` — Setup instructions
  - `quick-start.md` — Quick start guide
- `docs/reactive/` — Reactive programming guides
  - `signals.md` — Signal primitives
  - `effects.md` — Effects and side effects
  - `derived.md` — Derived values and computed properties
  - `advanced.md` — Advanced reactive patterns
- `docs/integrations/` — Framework integrations
  - `demo.md` — Demo project example
  - `flask.md` — Flask integration
  - `uvicorn.md` — ASGI/Uvicorn integration
  - `mcp.md` — MCP server integration
- `docs/references/` — Reference documentation
  - `index.md` — References index
  - `api.md` — API reference (coming soon)
- `docs/contributing.md` — This file

### Building Locally

```sh
zensical build
```

Then open `site/index.html` in your browser.

### Link Format

Use relative links between docs:

```markdown
[Learn more](../getting-started/installation.md) [Advanced patterns](../reactive/advanced.md)
```

For links from subdirectories, use `../` to go up:

```markdown
[Previous section](../index.md) [Related guide](../integrations/uvicorn.md)
```

## Common Tasks

### Adding a New Reactive Primitive

1. Implement in `reactivity/primitives.py`
2. Export from `reactivity/__init__.py`
3. Add tests in `tests/`
4. Document in `docs/learn/reactivity.md` or `docs/learn/signals-effects.md`

### Fixing a Bug

1. Add a test that reproduces the bug
2. Fix the bug
3. Verify test passes
4. Describe the fix in PR

### Updating Documentation

1. Edit relevant `.md` file in the appropriate `docs/` subdirectory
2. Build with `zensical build`
3. Review changes in `site/`
4. Commit and push

### Adding a New Framework Integration

1. Create `docs/ecosystem/framework-name.md`
2. Include setup, examples, and best practices
3. Update `docs/index.md` to link to it
4. Add integration code in the main repository

## Getting Help

- Check [existing issues](https://github.com/promplate/hmr/issues)
- Start a [discussion](https://github.com/promplate/hmr/discussions)
- Review the [source code](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr)

## Core Team

HMR is maintained by the Promplate community. See [contributors](https://github.com/promplate/hmr/graphs/contributors).

## License

By contributing, you agree your contributions will be licensed under the same license as the project.

---

Thank you for contributing to HMR!
