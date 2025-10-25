from contextlib import suppress
from pathlib import Path

from anyio import run, sleep
from fastmcp import Client

config = {
    "mcpServers": {
        "": {
            "transport": "stdio",
            "command": "mcp-hmr",
            "args": [f"{Path(__file__).parent / 'main.py'}:app"],
        },
    }
}


with suppress(KeyboardInterrupt):

    @run
    async def _():
        async with Client(config) as client:
            while True:
                print("\033c", end="")  # clear the console

                print("from tool echo:", (await client.call_tool("echo", {"message": "Hello from HMR!"})).content[0].text)  # type: ignore
                print("from resource example://greet:", (await client.read_resource("example://greet"))[0].text)  # type: ignore

                await sleep(1)
