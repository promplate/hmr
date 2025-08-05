from time import sleep

from sanic import Blueprint
from sanic.response import json

a = Blueprint("a", url_prefix="/a")


sleep(1)
print("slow module a.py imported")


@a.route("/")
async def index(request):
    return json({"message": "Hello from a.py!"})