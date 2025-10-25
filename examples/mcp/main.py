from fastmcp import FastMCP

# from mcp.server.fastmcp import FastMCP  # FastMCP v1 is also supported!


app = FastMCP()


@app.tool()
def echo(message: str):
    return message


@app.resource("example://greet")
def greet():
    return "hello world"


# `mcp-hmr main:app` is equivalent to:

if __name__ == "__main__":
    app.run("stdio")
