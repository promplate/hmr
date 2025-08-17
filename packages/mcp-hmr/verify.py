#!/usr/bin/env python3
"""Verification script for mcp-hmr functionality."""

import sys
from pathlib import Path

# Add the package to Python path
sys.path.insert(0, str(Path(__file__).parent))

print("🔍 MCP-HMR Verification Test")
print("=" * 50)

# Test 1: Package imports
print("\n1️⃣ Testing package imports...")
try:
    from mcp_hmr import begin_module_declare, finalize_module_declare, hmr_resource, hmr_tool
    print("✅ Core imports successful")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Test 2: CLI availability
print("\n2️⃣ Testing CLI availability...")
try:
    import mcp_hmr.cli  # noqa: F401
    print("✅ CLI module available")
except ImportError as e:
    print(f"❌ CLI import failed: {e}")
    sys.exit(1)

# Test 3: Registry functionality
print("\n3️⃣ Testing registry functionality...")
try:
    _gen = begin_module_declare()

    @hmr_tool()
    def test_tool(x: int) -> int:
        return x * 2

    @hmr_resource(uri_template="test://{id}")
    def test_resource(id: str) -> str:
        return f"Resource {id}"

    finalize_module_declare(_gen)

    from mcp_hmr.registry import get_registered_resources, get_registered_tools
    tools = get_registered_tools()
    resources = get_registered_resources()

    if "test_tool" in tools and "test_resource" in resources:
        print("✅ Registry registration successful")
        print(f"   - Tools: {list(tools.keys())}")
        print(f"   - Resources: {list(resources.keys())}")
    else:
        print("❌ Registry registration failed")
        sys.exit(1)

except Exception as e:
    print(f"❌ Registry test failed: {e}")
    sys.exit(1)

# Test 4: Reloader functionality
print("\n4️⃣ Testing reloader functionality...")
try:
    from mcp_hmr.reloader import create_mcp_reloader
    reloader = create_mcp_reloader(__file__)
    print("✅ Reloader creation successful")
except Exception as e:
    print(f"❌ Reloader test failed: {e}")
    sys.exit(1)

# Test 5: Notifications functionality
print("\n5️⃣ Testing notifications functionality...")
try:
    from mcp_hmr.notifications import send_capability_change_notification
    changes = {
        "tools_added": ["new_tool"],
        "tools_removed": [],
        "tools_updated": ["existing_tool"],
        "resources_added": [],
        "resources_removed": [],
        "resources_updated": []
    }
    send_capability_change_notification(changes)
    print("✅ Notifications functionality working")
except Exception as e:
    print(f"❌ Notifications test failed: {e}")
    sys.exit(1)

# Test 6: Utils functionality
print("\n6️⃣ Testing utilities functionality...")
try:
    from mcp_hmr.utils import generate_json_schema_from_function

    def sample_func(x: int, y: str = "default") -> bool:  # noqa: ARG001
        return True

    schema = generate_json_schema_from_function(sample_func)
    if schema and "properties" in schema:
        print("✅ Schema generation working")
        print(f"   - Generated schema for sample_func: {len(schema['properties'])} parameters")
    else:
        print("❌ Schema generation failed")
        sys.exit(1)
except Exception as e:
    print(f"❌ Utils test failed: {e}")
    sys.exit(1)

print("\n🎉 All tests passed! MCP-HMR is working correctly.")
print("=" * 50)
print("\n📚 Usage examples:")
print("  # Start MCP server with hot reloading:")
print("  mcp-hmr my_server:create_server")
print("  \n  # With custom watch paths:")
print("  mcp-hmr my_server:create_server --reload-include src --reload-exclude .venv")
print("\n✨ Ready to use MCP-HMR for your hot-reloadable MCP servers!")
