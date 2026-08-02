from pathlib import Path

from fastmcp import FastMCP

# from mcp.server.fastmcp import FastMCP  # FastMCP v1 is also supported!
# from mcp.server import MCPServer  # `mcp` 2.x is also supported!
# from mcp_use import MCPServer  # mcp-use is also supported!


app = FastMCP()

# read at import time, so HMR tracks it: editing `greeting.txt` reloads the module just like editing this file would
greeting = Path(__file__).parent.joinpath("greeting.txt").read_text()


@app.tool()
def echo(message: str):
    return message


@app.resource("example://greet")
def greet():
    return greeting


# `mcp-hmr main:app` is equivalent to:

if __name__ == "__main__":
    app.run("stdio")
