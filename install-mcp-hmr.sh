#!/bin/bash

# MCP-HMR Installation Script
# This script installs the mcp-hmr library and its dependencies

set -e

echo "🚀 Installing MCP-HMR..."
echo "========================"

# Check if we're in the correct directory
if [ ! -d "packages/mcp-hmr" ]; then
    echo "❌ Error: packages/mcp-hmr directory not found"
    echo "Please run this script from the project root directory"
    exit 1
fi

# Install the mcp-hmr package in development mode
echo "📦 Installing mcp-hmr package..."
cd packages/mcp-hmr

# Try to install with pip, handle externally-managed environments
if pip install -e . 2>/dev/null; then
    echo "✅ Installed with pip"
elif pip install -e . --break-system-packages 2>/dev/null; then
    echo "✅ Installed with --break-system-packages flag"
else
    echo "⚠️  Direct pip install failed. Trying alternative methods..."
    
    # Check if uv is available
    if command -v uv &> /dev/null; then
        echo "📦 Using uv for installation..."
        uv pip install -e .
    # Check if pipx is available
    elif command -v pipx &> /dev/null; then
        echo "📦 Using pipx for installation..."
        pipx install -e .
    else
        echo "❌ Could not install automatically."
        echo "Please manually create a virtual environment:"
        echo "  python3 -m venv venv"
        echo "  source venv/bin/activate"
        echo "  pip install -e ."
        exit 1
    fi
fi

echo "✅ Installation completed!"
echo ""
echo "🎯 Quick start:"
echo "  1. cd examples/mcp-demo"
echo "  2. mcp-hmr server:main --clear"
echo "  3. In another terminal: python3 client.py"
echo ""
echo "📚 See README-mcp-hmr.md for full documentation"