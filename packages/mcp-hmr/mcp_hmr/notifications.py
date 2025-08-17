"""Protocol notification handling for MCP capability changes."""

import json
from datetime import UTC
from logging import getLogger
from typing import Any

logger = getLogger("mcp-hmr.notifications")


class MCPNotificationHandler:
    """Handles sending MCP protocol notifications for capability changes."""

    def __init__(self):
        self._notification_queue: list[dict[str, Any]] = []
        self._supports_dynamic_capabilities = False

    def set_capability_support(self, *, supports_dynamic: bool) -> None:
        """Set whether the MCP client supports dynamic capability changes.

        Args:
            supports_dynamic: True if client supports dynamic capability updates
        """
        self._supports_dynamic_capabilities = supports_dynamic
        logger.debug("Dynamic capability support set to: %s", supports_dynamic)

    def queue_capability_change(self, changes: dict[str, Any]) -> None:
        """Queue a capability change notification.

        Args:
            changes: Dictionary containing capability changes
        """
        notification = {
            "method": "notifications/capabilities/changed",
            "params": {
                "timestamp": self._get_timestamp(),
                "changes": changes
            }
        }
        self._notification_queue.append(notification)
        logger.debug("Queued capability change notification: %s", changes)

    def send_queued_notifications(self) -> list[dict[str, Any]]:
        """Send all queued notifications and return them.

        Returns:
            List of notification dictionaries that were sent
        """
        if not self._supports_dynamic_capabilities:
            if self._notification_queue:
                logger.warning(
                    "MCP client does not support dynamic capabilities. "
                    "Queued %d notifications that cannot be sent.",
                    len(self._notification_queue)
                )
            sent = []
        else:
            sent = self._notification_queue.copy()
            for notification in sent:
                self._send_notification(notification)

        self._notification_queue.clear()
        return sent

    def _send_notification(self, notification: dict[str, Any]) -> None:
        """Send a single notification to the MCP client.

        Args:
            notification: The notification dictionary to send
        """
        # In a real implementation, this would send the notification
        # through the MCP transport layer (stdio, sse, websocket, etc.)
        # For now, we'll just log it
        logger.info("Sending MCP notification: %s", json.dumps(notification, indent=2))

        # TODO: Integrate with actual MCP transport implementation
        # This would depend on the specific MCP library being used

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        from datetime import datetime
        return datetime.now(UTC).isoformat()


# Global notification handler instance
_notification_handler = MCPNotificationHandler()


def set_mcp_capability_support(*, supports_dynamic: bool) -> None:
    """Set whether the MCP client supports dynamic capability changes.

    Args:
        supports_dynamic: True if client supports dynamic capability updates
    """
    _notification_handler.set_capability_support(supports_dynamic=supports_dynamic)


def send_capability_change_notification(changes: dict[str, Any]) -> None:
    """Send a capability change notification to the MCP client.

    Args:
        changes: Dictionary containing capability changes with keys:
            - tools_added: List of new tool names
            - tools_removed: List of removed tool names
            - tools_updated: List of modified tool names
            - resources_added: List of new resource names
            - resources_removed: List of removed resource names
            - resources_updated: List of modified resource names
    """
    # Filter out empty change lists to avoid sending unnecessary notifications
    filtered_changes = {k: v for k, v in changes.items() if v}

    if not filtered_changes:
        logger.debug("No capability changes to notify")
        return

    _notification_handler.queue_capability_change(filtered_changes)
    sent_notifications = _notification_handler.send_queued_notifications()

    if sent_notifications:
        logger.info("Sent %d capability change notification(s)", len(sent_notifications))
    else:
        logger.warning(
            "Capability changes detected but could not send notifications. "
            "This may be because the MCP client does not support dynamic capabilities."
        )


def get_capability_change_summary(changes: dict[str, Any]) -> str:
    """Generate a human-readable summary of capability changes.

    Args:
        changes: Dictionary containing capability changes

    Returns:
        Human-readable summary string
    """
    parts = []

    # Tool changes
    if changes.get("tools_added"):
        parts.append(f"Added {len(changes['tools_added'])} tool(s)")
    if changes.get("tools_removed"):
        parts.append(f"Removed {len(changes['tools_removed'])} tool(s)")
    if changes.get("tools_updated"):
        parts.append(f"Updated {len(changes['tools_updated'])} tool(s)")

    # Resource changes
    if changes.get("resources_added"):
        parts.append(f"Added {len(changes['resources_added'])} resource(s)")
    if changes.get("resources_removed"):
        parts.append(f"Removed {len(changes['resources_removed'])} resource(s)")
    if changes.get("resources_updated"):
        parts.append(f"Updated {len(changes['resources_updated'])} resource(s)")

    if not parts:
        return "No capability changes"

    return "; ".join(parts)


def create_tools_list_notification(tools: list[str]) -> dict[str, Any]:
    """Create a tools/list notification for the current set of tools.

    Args:
        tools: List of tool names

    Returns:
        MCP notification dictionary
    """
    return {
        "method": "notifications/tools/list_changed",
        "params": {
            "tools": tools,
            "timestamp": _notification_handler._get_timestamp()  # noqa: SLF001
        }
    }


def create_resources_list_notification(resources: list[str]) -> dict[str, Any]:
    """Create a resources/list notification for the current set of resources.

    Args:
        resources: List of resource names

    Returns:
        MCP notification dictionary
    """
    return {
        "method": "notifications/resources/list_changed",
        "params": {
            "resources": resources,
            "timestamp": _notification_handler._get_timestamp()  # noqa: SLF001
        }
    }
