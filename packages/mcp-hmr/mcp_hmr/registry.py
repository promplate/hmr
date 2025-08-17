"""Reactive registry for MCP tools and resources using hmr's fine-grained reactivity."""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from reactivity.helpers import Reactive
from reactivity.primitives import Signal

F = TypeVar("F", bound=Callable[..., Any])

# Global registry for tracking tools and resources across all modules
_global_registry = Reactive({
    "tools": {},
    "resources": {},
    "modules": {},  # Track which module owns which tools/resources
})


def _get_registry():
    """Get the current registry state."""
    return _global_registry


def _update_registry(new_data):
    """Update the registry state."""
    _global_registry.update(new_data)


class ModuleGeneration:
    """Tracks the generation of a module for cleanup purposes."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.tools: list[str] = []
        self.resources: list[str] = []

    def cleanup(self):
        """Remove all tools and resources registered by this module generation."""
        registry = _get_registry()

        # Remove tools
        for tool_name in self.tools:
            if tool_name in registry["tools"]:
                del registry["tools"][tool_name]

        # Remove resources
        for resource_name in self.resources:
            if resource_name in registry["resources"]:
                del registry["resources"][resource_name]

        # Update the registry
        _update_registry(registry)


def begin_module_declare() -> ModuleGeneration:
    """Begin declaring tools and resources for a module.

    Returns a generation object that should be passed to finalize_module_declare.
    """
    frame = inspect.currentframe()
    module_name = frame.f_back.f_globals.get("__name__", "unknown") if frame and frame.f_back else "unknown"

    return ModuleGeneration(module_name)


def finalize_module_declare(generation: ModuleGeneration):
    """Finalize module declaration and clean up previous generations.

    This should be called at the end of module-level code to ensure
    old tool/resource registrations are cleaned up.
    """
    registry = _get_registry()
    module_name = generation.module_name

    # Clean up previous generation if it exists
    if module_name in registry["modules"]:
        old_generation = registry["modules"][module_name]
        if isinstance(old_generation, ModuleGeneration):
            old_generation.cleanup()

    # Register new generation
    registry["modules"][module_name] = generation
    _update_registry(registry)


def hmr_tool(name: str | None = None, *, schema: dict | None = None) -> Callable[[F], F]:
    """Decorator to register an MCP tool with hot reloading support.

    Args:
        name: Optional name for the tool. If not provided, uses function name.
        schema: Optional JSON schema for tool parameters.

    Returns:
        Decorated function that will be tracked in the reactive registry.
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__

        # Create a reactive signal for this tool function
        tool_signal = Signal(func)

        # Wrap the function to use the reactive signal
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_func = tool_signal()
            return current_func(*args, **kwargs)

        # Store tool metadata in the registry
        registry = _get_registry()
        registry["tools"][tool_name] = {
            "function": tool_signal,
            "wrapper": wrapper,
            "schema": schema,
            "name": tool_name,
        }
        _update_registry(registry)

        # Track this tool in the current module generation
        frame = inspect.currentframe()
        if frame and frame.f_back:
            module_locals = frame.f_back.f_locals
            # Look for a generation object in the calling scope
            for _var_name, var_value in module_locals.items():
                if isinstance(var_value, ModuleGeneration):
                    var_value.tools.append(tool_name)
                    break

        # Update the signal when the function changes during reload
        original_signal_set = tool_signal.set

        def reactive_set(new_func):
            original_signal_set(new_func)
            # Trigger any change notifications here if needed

        tool_signal.set = reactive_set

        return wrapper

    return decorator


def hmr_resource(name: str | None = None, *, uri_template: str | None = None) -> Callable[[F], F]:
    """Decorator to register an MCP resource with hot reloading support.

    Args:
        name: Optional name for the resource. If not provided, uses function name.
        uri_template: Optional URI template for the resource.

    Returns:
        Decorated function that will be tracked in the reactive registry.
    """
    def decorator(func: F) -> F:
        resource_name = name or func.__name__

        # Create a reactive signal for this resource function
        resource_signal = Signal(func)

        # Wrap the function to use the reactive signal
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_func = resource_signal()
            return current_func(*args, **kwargs)

        # Store resource metadata in the registry
        registry = _get_registry()
        registry["resources"][resource_name] = {
            "function": resource_signal,
            "wrapper": wrapper,
            "uri_template": uri_template,
            "name": resource_name,
        }
        _update_registry(registry)

        # Track this resource in the current module generation
        frame = inspect.currentframe()
        if frame and frame.f_back:
            module_locals = frame.f_back.f_locals
            # Look for a generation object in the calling scope
            for _var_name, var_value in module_locals.items():
                if isinstance(var_value, ModuleGeneration):
                    var_value.resources.append(resource_name)
                    break

        # Update the signal when the function changes during reload
        original_signal_set = resource_signal.set

        def reactive_set(new_func):
            original_signal_set(new_func)
            # Trigger any change notifications here if needed

        resource_signal.set = reactive_set

        return wrapper

    return decorator


def get_registered_tools() -> dict[str, dict]:
    """Get all currently registered tools."""
    registry = _get_registry()
    return registry["tools"].copy()


def get_registered_resources() -> dict[str, dict]:
    """Get all currently registered resources."""
    registry = _get_registry()
    return registry["resources"].copy()


def get_tool_changes() -> dict[str, str]:
    """Get a summary of tool changes (added, removed, updated).

    This would be called by the reloader to detect changes.
    """
    # This is a simplified implementation
    # In a real implementation, you'd compare with a previous snapshot
    tools = get_registered_tools()
    return dict.fromkeys(tools, "current")


def get_resource_changes() -> dict[str, str]:
    """Get a summary of resource changes (added, removed, updated).

    This would be called by the reloader to detect changes.
    """
    # This is a simplified implementation
    # In a real implementation, you'd compare with a previous snapshot
    resources = get_registered_resources()
    return dict.fromkeys(resources, "current")
