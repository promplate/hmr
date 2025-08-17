"""MCP-HMR: Hot Module Reloading for Model Context Protocol servers."""

from .registry import begin_module_declare, finalize_module_declare, hmr_resource, hmr_tool

__version__ = "0.1.0"

__all__ = [
    "begin_module_declare",
    "finalize_module_declare",
    "hmr_resource",
    "hmr_tool",
]
