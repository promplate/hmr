# MCP Server

Use HMR with the MCP (Model Context Protocol) server for AI-powered development assistance.

## Installation

```sh
pip install mcp-hmr
```

## Overview

The `mcp-hmr` package integrates HMR with Claude's Model Context Protocol, allowing AI assistants to understand and work with your Python code.

## Setup

1. Configure your MCP settings to use `mcp-hmr`
2. Start your development server
3. Claude can now access your code's reactive state and module graph

## Key Features

- **Code awareness**: Claude understands your module dependencies
- **Reactive context**: AI agents see your signal/effect/derived state
- **Live updates**: Changes are reflected in real-time
- **State inspection**: AI can query module globals and state

## How It Works

When you run HMR with MCP enabled:

1. HMR tracks module dependencies and state changes
2. MCP server exposes this information to Claude
3. Claude can provide better suggestions based on actual runtime state
4. Changes you make are immediately visible to the AI

## Example Workflow

```text
1. You run: hmr main.py
2. HMR tracks changes and module graph
3. MCP server starts (if enabled)
4. Claude can query your code:
   - "What modules import this function?"
   - "Show me all signals in the current scope"
   - "What changed when I edited this file?"
5. You get context-aware suggestions
```

## Configuration

See `examples/mcp/` for configuration examples and setup instructions.

## Using with Claude

When Claude has access to your MCP server, you can:

- Ask about your code structure
- Get refactoring suggestions based on actual dependencies
- Debug issues with AI assistance
- Get explanations of how HMR affects your code

## Examples

See `examples/mcp/` for:

- `main.py`: Example MCP server setup
- `client.py`: MCP client implementation
- `mcp.json`: Configuration file

Learn more: [Flask Integration](./flask.md) | [Reactive Primitives](../reactive/signals.md) | [ASGI Integration](./uvicorn.md)
