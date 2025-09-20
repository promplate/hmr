import asyncio
import sys

from logic import greet


async def _readline() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


async def serve() -> None:  # entry for `mcp-hmr`
    print("MCP demo server started. Type a name and press Enter (Ctrl+C to stop)", flush=True)
    try:
        while True:
            line = (await _readline()).strip()
            if not line:
                continue
            print(f"Response: {greet(line)}", flush=True)
    except asyncio.CancelledError:  # graceful cancellation on reload
        pass
