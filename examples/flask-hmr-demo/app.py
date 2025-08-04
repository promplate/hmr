from flask import Flask

app = Flask(__name__)

@app.route("/")
def hello():
    return "Hello from Flask with HMR!"

@app.route("/api/status")
def status():
    return {"status": "running", "hmr": "enabled"}

if __name__ == "__main__":
    # Instead of app.run(), use flask-hmr:
    # flask-hmr app:app
    app.run(debug=True)