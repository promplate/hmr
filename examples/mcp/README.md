# MCP Example

This example demonstrates how to use `mcp-hmr` with a FastMCP server.

First, install the required dependencies and activate the venv:

```sh
uv sync
source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
```

## Demo 1 - with `@modelcontextprotocol/inspector`

First, open the inspector with `npx` / `pnpx` / `bunx`, etc:

```sh
npx @modelcontextprotocol/inspector --config mcp.json --server main
```

Click the "Connect" button in the inspector UI. It will connect to the MCP server with `mcp-hmr main.py:app`.

<img width="3200" height="1116" alt="The MCP Inspector" src="https://github.com/user-attachments/assets/3e96039f-2720-4b14-be4d-2a54678d49dc" />

Now you can view the resources and tools as normal. Then change them in `main.py` and save the file WITHOUT clicking "Restart" in the inspector. Any call to get the resource / run the tool will reflect the updated code.

https://github.com/user-attachments/assets/fe8810a3-916d-49ab-91e5-5a595ae0b3f9

## Demo 2 - with FastMCP client

Run the client to start and use the server:

```sh
python client.py
```

It uses the MCP server defined in `main.py`. Now open your favorite editor and modify `main.py` to see the printing update in real-time!

## What to Observe

- Try changing the `echo` tool's return value or the `greet` resource's content.
- You will see the client output update to reflect your changes without restarting the connection.

This demo shows how to use `mcp-hmr` to enable seamless hot reloading for MCP servers, maintaining the connection between client and server while updating the code on-the-fly.
