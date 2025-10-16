# Hypercorn HMR Example

This example demonstrates how to use `hypercorn-hmr` as a drop-in replacement for `hypercorn --reload`.

## Simple FastAPI Application

```python
# app.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World", "server": "hypercorn-hmr"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
```

## Usage Comparison

### Traditional Hypercorn with Reload
```bash
hypercorn app:app --reload --bind 127.0.0.1:8000 --log-level info
```

### Hypercorn with HMR (Enhanced)
```bash
hypercorn-hmr app:app --host 127.0.0.1 --port 8000 --log-level info
```

## Key Benefits

1. **Faster Reloads**: Only affected modules are reloaded, not the entire process
2. **Preserved State**: Application state and connections are maintained
3. **Fine-grained Updates**: Changes only trigger reloads for dependent modules
4. **Consistent Interface**: Same CLI parameters as uvicorn-hmr for easy migration

## Additional Features

- `--refresh`: Enable automatic browser page refreshing
- `--clear`: Clear terminal on reload
- `--reload-include` / `--reload-exclude`: Control watched files

## Testing the HMR

1. Start the server:
   ```bash
   hypercorn-hmr app:app
   ```

2. Make a request:
   ```bash
   curl http://localhost:8000/
   ```

3. Modify the response in `app.py`

4. Watch the server automatically reload and serve the updated response!