from sanic import Blueprint
from sanic.response import json

b = Blueprint("b", url_prefix="/b")


@b.route("/")
async def index(request):
    return json({"message": "Hello from b.py! (FINAL HMR TEST)", "data": "HMR is working perfectly with Sanic!"})


@b.route("/test")
async def test(request):
    return json({"message": "Test endpoint from b.py", "status": "working", "hmr": "fully operational"})