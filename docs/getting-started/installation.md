# Installation

Get `hmr` up and running in your project.

## Install

Choose your preferred package manager:

### pip

```sh
pip install hmr
```

### uv

```sh
uv pip install hmr
```

### pipx

For global CLI access:

```sh
pipx install hmr
```

### uvx

Run without installation:

```sh
uvx hmr <file.py>
```

## Virtual Environment

If you're using a virtual environment, it's recommended to install `hmr` inside it rather than globally:

```sh
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install hmr
```

## Framework-Specific Installs

### ASGI Applications

For automatic browser refresh and ASGI support:

```sh
pip install uvicorn-hmr[all]
```

### Flask Applications

```sh
pip install fastapi-reloader
```

## Verify Installation

```sh
hmr --help
```

You should see the usage information. Now proceed to [Quick Start](./quick-start.md).
