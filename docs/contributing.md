---
icon: lucide/handshake
---

# Contributing

## Choose the repository and branch

- This repository contains the integration packages in `packages/` and runnable examples in `examples/`. Start integration changes from `readme`, the default branch.
- The documentation site lives on the `docs` branch. Start documentation changes from `docs` and target that branch in the pull request.
- The core `hmr` package, including `reactivity`, lives in [promplate/pyth-on-line under `packages/hmr`](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr). Changes to reactive primitives or the core loader belong there, not in this repository.

## Set up this repository

Fork and clone the repository, then choose the appropriate base branch. For documentation:

```sh
git clone https://github.com/YOUR_USERNAME/hmr.git
cd hmr
git switch docs
git switch -c fix/your-docs-change
uv sync --all-packages
```

Use Python 3.12 or later. `uv sync --all-packages` creates the virtual environment and installs the workspace packages. Run commands through `uv run` so they use that environment. There is no `dev` extra to install with `pip install -e ".[dev]"`.

## Run the checks

The repository's [CI workflow](https://github.com/promplate/hmr/blob/docs/.github/workflows/ci.yml) runs formatting, linting, and type checks:

```sh
uv run ruff format --check --diff
uv run ruff check --diff
uv run -- bunx basedpyright
```

The last command requires Bun. CI also runs a spelling check with `typos`. Follow the workflow on the branch you are changing for its current Python and operating-system matrix.

For documentation changes, build the site:

```sh
uv run zensical build
```

To preview it locally:

```sh
uv run zensical serve
```

The documentation dependencies and configuration are on `docs`; these commands are not a setup guide for the default branch.

## Verify behavior

Keep changes focused and run the affected example or a small reproduction. For reload changes, check both the initial run and a subsequent source edit, including whether state that should survive is preserved. The current repository CI is not a general `pytest` suite; do not mistake passing lint and type checks for runtime validation.

Run the checked-in documentation regression tests as well:

```sh
uv run python -W error::RuntimeWarning -m unittest discover -s tests -v
```

These tests cover selected reactive and loader semantics; see `tests/README.md` for limits.

For changes to the core package in `promplate/pyth-on-line`, follow that repository's setup and checks instead.

## Submit the change

Describe what changed, why it is needed, the checks you ran, and any remaining limitations. Target `docs` for site changes and `readme` for this repository's integration changes. Add or update the relevant documentation and examples alongside behavior changes.
