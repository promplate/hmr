"""Helper utilities for mcp-hmr."""

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def find_mcp_server_files(
    search_paths: list[str] | None = None,
    patterns: list[str] | None = None
) -> list[Path]:
    """Find potential MCP server files in the given search paths.

    Args:
        search_paths: Directories to search in (defaults to current directory)
        patterns: File name patterns to match (defaults to common MCP server patterns)

    Returns:
        List of Path objects for potential MCP server files
    """
    if search_paths is None:
        search_paths = ["."]

    if patterns is None:
        patterns = [
            "**/server.py",
            "**/mcp_server.py",
            "**/main.py",
            "**/*_server.py",
            "**/*_mcp.py"
        ]

    found_files = []
    for search_path in search_paths:
        path = Path(search_path)
        if not path.exists():
            continue

        for pattern in patterns:
            found_files.extend(path.glob(pattern))

    # Remove duplicates and return sorted list
    return sorted(set(found_files))


def get_function_signature_info(func: Callable) -> dict[str, Any]:
    """Extract signature information from a function for MCP tool registration.

    Args:
        func: Function to analyze

    Returns:
        Dictionary containing signature information
    """
    sig = inspect.signature(func)

    info = {
        "name": func.__name__,
        "parameters": {},
        "return_annotation": None,
        "docstring": inspect.getdoc(func),
    }

    # Extract parameter information
    for param_name, param in sig.parameters.items():
        param_info = {
            "annotation": param.annotation if param.annotation != inspect.Parameter.empty else None,
            "default": param.default if param.default != inspect.Parameter.empty else None,
            "kind": param.kind.name,
        }
        info["parameters"][param_name] = param_info

    # Extract return annotation
    if sig.return_annotation != inspect.Signature.empty:
        info["return_annotation"] = sig.return_annotation

    return info


def generate_json_schema_from_function(func: Callable) -> dict[str, Any]:
    """Generate a basic JSON schema from a function signature.

    This is a simplified schema generator for MCP tool registration.
    For production use, consider using a more robust schema generation library.

    Args:
        func: Function to generate schema for

    Returns:
        JSON schema dictionary
    """
    sig_info = get_function_signature_info(func)

    schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # Add description from docstring
    if sig_info["docstring"]:
        schema["description"] = sig_info["docstring"]

    # Convert parameters to JSON schema properties
    for param_name, param_info in sig_info["parameters"].items():
        prop = {}

        # Map Python types to JSON schema types
        annotation = param_info["annotation"]
        if annotation:
            if annotation is int:
                prop["type"] = "integer"
            elif annotation is float:
                prop["type"] = "number"
            elif annotation is str:
                prop["type"] = "string"
            elif annotation is bool:
                prop["type"] = "boolean"
            elif annotation is list:
                prop["type"] = "array"
            elif annotation is dict:
                prop["type"] = "object"
            else:
                # For complex types, default to string with description
                prop["type"] = "string"
                prop["description"] = f"Parameter of type {annotation}"
        else:
            prop["type"] = "string"  # Default fallback

        # Add to required if no default value
        if param_info["default"] is None:
            schema["required"].append(param_name)

        schema["properties"][param_name] = prop

    return schema


def create_mcp_tool_schema(
    name: str,
    description: str,
    parameters: dict[str, Any]
) -> dict[str, Any]:
    """Create a proper MCP tool schema.

    Args:
        name: Tool name
        description: Tool description
        parameters: Parameter schema

    Returns:
        MCP tool schema dictionary
    """
    return {
        "name": name,
        "description": description,
        "inputSchema": parameters
    }


def create_mcp_resource_schema(
    uri: str,
    name: str | None = None,
    description: str | None = None,
    mime_type: str | None = None
) -> dict[str, Any]:
    """Create a proper MCP resource schema.

    Args:
        uri: Resource URI
        name: Optional resource name
        description: Optional resource description
        mime_type: Optional MIME type

    Returns:
        MCP resource schema dictionary
    """
    schema = {"uri": uri}

    if name:
        schema["name"] = name
    if description:
        schema["description"] = description
    if mime_type:
        schema["mimeType"] = mime_type

    return schema


def is_mcp_server_module(module_path: Path) -> bool:
    """Check if a Python module appears to be an MCP server.

    This does a simple heuristic check by looking for MCP-related imports
    and function patterns.

    Args:
        module_path: Path to the Python module

    Returns:
        True if the module appears to be an MCP server
    """
    if not module_path.exists() or module_path.suffix != ".py":
        return False

    try:
        content = module_path.read_text(encoding="utf-8")

        # Look for common MCP patterns
        mcp_patterns = [
            "mcp",
            "model_context_protocol",
            "create_server",
            "mcp_server",
            "tool(",
            "resource(",
            "@tool",
            "@resource",
        ]

        content_lower = content.lower()
        return any(pattern in content_lower for pattern in mcp_patterns)

    except Exception:
        return False


def get_calling_module_name() -> str:
    """Get the name of the module that called the current function.

    Returns:
        Module name or "unknown" if it can't be determined
    """
    frame = inspect.currentframe()
    try:
        if frame and frame.f_back and frame.f_back.f_back:
            return frame.f_back.f_back.f_globals.get("__name__", "unknown")
        return "unknown"
    finally:
        del frame


def safe_import_module(module_name: str) -> Any | None:
    """Safely import a module without raising exceptions.

    Args:
        module_name: Name of the module to import

    Returns:
        Imported module or None if import failed
    """
    try:
        return __import__(module_name)
    except Exception:
        return None


def format_function_call(func: Callable, *args, **kwargs) -> str:
    """Format a function call for logging purposes.

    Args:
        func: Function being called
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        Formatted function call string
    """
    arg_strs = []

    # Add positional arguments
    arg_strs.extend(repr(arg) for arg in args)

    # Add keyword arguments
    arg_strs.extend(f"{k}={v!r}" for k, v in kwargs.items())

    args_str = ", ".join(arg_strs)
    return f"{func.__name__}({args_str})"


def truncate_string(s: str, max_length: int = 100) -> str:
    """Truncate a string for display purposes.

    Args:
        s: String to truncate
        max_length: Maximum length before truncation

    Returns:
        Truncated string with "..." if needed
    """
    if len(s) <= max_length:
        return s
    return s[:max_length - 3] + "..."
