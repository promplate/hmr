# Contributing

We welcome contributions. This guide covers the workflow and standards.

## Setup

```sh
git clone https://github.com/YOUR_USERNAME/hmr.git
cd hmr
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Workflow

1. Branch: `git checkout -b feature/your-name`
2. Make focused changes. Add tests for new behavior.
3. Run checks locally:

```sh
pytest
ruff check .
ruff format .
```

4. (Optional) Build docs to preview:

```sh
zensical build  # outputs to site/
```

5. Commit with concise, lowercase messages under 80 characters, no trailing period.
6. Push and open a PR with a clear description and any related issue links.

## Code standards

- Target Python 3.12+.
- Follow PEP 8; prefer `pathlib` over `os.path`; use explicit imports.
- Type public APIs.
- Keep docs concise; prefer clear names over redundant docstrings.

## Testing

- Tests in `tests/`, files start with `test_`.
- Use descriptive test names.
- Example:

```python
from reactivity import effect, signal

def test_signal_notifies_on_change():
    s = signal(0)
    seen = []
    effect(lambda: seen.append(s.get()))
    s.set(1)
    assert seen == [0, 1]
```

## Documentation

- Edit `.md` files under `docs/`.
- Build with `zensical build` to preview.
- Structure: `docs/getting-started/`, `docs/reactive/`, `docs/integrations/`, `docs/references/`.

## Common tasks

- New primitive: implement in `reactivity/primitives.py`, export, add tests, document in `docs/reactive/`.
- Bug fix: add a failing test, fix, verify, describe in PR.
- New integration: add `docs/integrations/<name>.md`, link from index, add example under `examples/` or `packages/`.

## Help

- Check existing issues and discussions.
- Review source under `reactivity/` and `packages/`.

## License

Contributions are licensed under the project's existing license.
