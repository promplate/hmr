# Flask HMR Demo

This is a simple example showing how to use flask-hmr.

## Usage

Instead of:
```bash
python app.py
# or
flask run
```

Use:
```bash
flask-hmr app:app
```

## Features

- Hot module replacement for Flask applications
- Faster reloads compared to Flask's built-in reload
- Preserves application state where possible

## Test the HMR

1. Start the server: `flask-hmr app:app`
2. Visit http://127.0.0.1:5000
3. Modify the hello() function in app.py
4. See the changes reflected immediately without manual restart