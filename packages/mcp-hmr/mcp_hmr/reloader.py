"""Core reloader logic for MCP servers extending hmr's SyncReloader."""

from collections.abc import Callable
from logging import getLogger
from pathlib import Path
from typing import Any

from reactivity.hmr.core import SyncReloader

from .notifications import send_capability_change_notification
from .registry import get_registered_resources, get_registered_tools

logger = getLogger("mcp-hmr.reloader")


class MCPReloader(SyncReloader):
    """Reloader specifically designed for MCP servers.

    Unlike uvicorn-hmr which restarts the server process, this reloader:
    - Keeps the MCP server running continuously
    - Incrementally replaces tool functions/resource providers
    - Sends capability change notifications to clients
    - Preserves long-lived state (model loading, connection pools, caches)
    """

    def __init__(
        self,
        entry_file: str,
        reload_include: list[str],
        reload_exclude: list[str],
        on_change_callback: Callable[[], None] | None = None,
    ):
        super().__init__(entry_file, reload_include, reload_exclude)
        self.on_change_callback = on_change_callback
        self._last_tools_snapshot: dict[str, Any] = {}
        self._last_resources_snapshot: dict[str, Any] = {}
        self._preserved_state: dict[str, Any] = {}

    def preserve_state(self, key: str, value: Any) -> None:
        """Preserve state across reloads.

        Args:
            key: Unique identifier for the state
            value: The state object to preserve (should be pickleable or have weak references)
        """
        self._preserved_state[key] = value
        logger.debug("Preserved state for key: %s", key)

    def get_preserved_state(self, key: str, default: Any = None) -> Any:
        """Retrieve preserved state.

        Args:
            key: Unique identifier for the state
            default: Default value if key not found

        Returns:
            The preserved state object or default
        """
        return self._preserved_state.get(key, default)

    def clear_preserved_state(self, key: str | None = None) -> None:
        """Clear preserved state.

        Args:
            key: Specific key to clear, or None to clear all
        """
        if key is None:
            self._preserved_state.clear()
            logger.debug("Cleared all preserved state")
        else:
            self._preserved_state.pop(key, None)
            logger.debug("Cleared preserved state for key: %s", key)

    def _snapshot_capabilities(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Take a snapshot of current tools and resources."""
        tools = get_registered_tools()
        resources = get_registered_resources()
        return tools, resources

    def _detect_capability_changes(
        self,
        old_tools: dict[str, Any],
        new_tools: dict[str, Any],
        old_resources: dict[str, Any],
        new_resources: dict[str, Any]
    ) -> dict[str, Any]:
        """Detect changes in capabilities between snapshots.

        Returns:
            Dictionary describing the changes with keys:
            - tools_added: List of new tool names
            - tools_removed: List of removed tool names
            - tools_updated: List of modified tool names
            - resources_added: List of new resource names
            - resources_removed: List of removed resource names
            - resources_updated: List of modified resource names
        """
        changes = {
            "tools_added": [],
            "tools_removed": [],
            "tools_updated": [],
            "resources_added": [],
            "resources_removed": [],
            "resources_updated": [],
        }

        # Detect tool changes
        old_tool_names = set(old_tools.keys())
        new_tool_names = set(new_tools.keys())

        changes["tools_added"] = list(new_tool_names - old_tool_names)
        changes["tools_removed"] = list(old_tool_names - new_tool_names)

        # Check for updated tools (comparing function objects by id)
        for name in old_tool_names & new_tool_names:
            old_func = old_tools[name].get("function")
            new_func = new_tools[name].get("function")
            if old_func is not new_func:
                changes["tools_updated"].append(name)

        # Detect resource changes
        old_resource_names = set(old_resources.keys())
        new_resource_names = set(new_resources.keys())

        changes["resources_added"] = list(new_resource_names - old_resource_names)
        changes["resources_removed"] = list(old_resource_names - new_resource_names)

        # Check for updated resources
        for name in old_resource_names & new_resource_names:
            old_func = old_resources[name].get("function")
            new_func = new_resources[name].get("function")
            if old_func is not new_func:
                changes["resources_updated"].append(name)

        return changes

    def _log_capability_changes(self, changes: dict[str, Any]) -> None:
        """Log capability changes in a user-friendly format."""
        if changes["tools_added"]:
            logger.info("Added tools: %s", ", ".join(changes["tools_added"]))
        if changes["tools_removed"]:
            logger.info("Removed tools: %s", ", ".join(changes["tools_removed"]))
        if changes["tools_updated"]:
            logger.info("Updated tools: %s", ", ".join(changes["tools_updated"]))

        if changes["resources_added"]:
            logger.info("Added resources: %s", ", ".join(changes["resources_added"]))
        if changes["resources_removed"]:
            logger.info("Removed resources: %s", ", ".join(changes["resources_removed"]))
        if changes["resources_updated"]:
            logger.info("Updated resources: %s", ", ".join(changes["resources_updated"]))

    def on_events(self, events) -> None:
        """Handle file change events with MCP-specific logic."""
        if not events:
            return

        # Take snapshot before reload
        old_tools, old_resources = self._snapshot_capabilities()

        # Call parent implementation to handle the actual reloading
        super().on_events(events)

        # Take snapshot after reload
        new_tools, new_resources = self._snapshot_capabilities()

        # Detect and handle capability changes
        changes = self._detect_capability_changes(old_tools, new_tools, old_resources, new_resources)

        # Log changes
        self._log_capability_changes(changes)

        # Send capability change notifications if there are any changes
        if any(changes.values()):
            try:
                send_capability_change_notification(changes)
            except Exception as e:
                logger.warning("Failed to send capability change notification: %s", e)

        # Update snapshots for next time
        self._last_tools_snapshot = new_tools
        self._last_resources_snapshot = new_resources

        # Call custom change callback if provided
        if self.on_change_callback:
            try:
                self.on_change_callback()
            except Exception as e:
                logger.warning("Error in change callback: %s", e)

    def start_watching(self) -> None:
        """Start watching for file changes with MCP-specific initialization."""
        logger.info("Starting MCP-HMR file watcher...")

        # Take initial snapshot
        self._last_tools_snapshot, self._last_resources_snapshot = self._snapshot_capabilities()

        # Start the parent watcher
        super().start_watching()


def create_mcp_reloader(
    entry_file: str,
    reload_include: list[str] | None = None,
    reload_exclude: list[str] | None = None,
    on_change_callback: Callable[[], None] | None = None,
) -> MCPReloader:
    """Create an MCP reloader with sensible defaults.

    Args:
        entry_file: Path to the main MCP server file
        reload_include: Paths to include in watching (defaults to current directory)
        reload_exclude: Paths to exclude from watching (defaults to .venv)
        on_change_callback: Optional callback to call when changes are detected

    Returns:
        Configured MCPReloader instance
    """
    if reload_include is None:
        reload_include = [str(Path.cwd())]
    if reload_exclude is None:
        reload_exclude = [".venv"]

    return MCPReloader(entry_file, reload_include, reload_exclude, on_change_callback)
