from sanic import Sanic, response

def create_app():
    # Use a unique name each time to avoid conflicts
    import time
    app_name = f"ExampleApp_{int(time.time() * 1000)}"
    app = Sanic(app_name)

    @app.route("/")
    async def hello_world(request):
        return response.text("Hello, world! (modified with factory)")

    @app.route("/test")
    async def test_route(request):
        return response.json({"message": "This is a test route"})

    @app.route("/api/users/<user_id:int>")
    async def get_user(request, user_id):
        return response.json({"user_id": user_id, "name": f"User {user_id}"})

    return app

# Export the app instance
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)