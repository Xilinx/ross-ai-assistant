# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A stdio MCP server that publishes the three upstream Vivado tool names and nothing else.

ced_guard.py connects to its upstream at startup and refuses to serve unless the
upstream offers vivado_start, vivado_execute and vivado_stop, so a packaging test
that only wants to know whether the guard boots still needs something on the other
end. This stands in for the real server; it never starts Vivado.
"""
import asyncio
import sys

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

TOOLS = [types.Tool(name=name, description='stand-in for the upstream Vivado MCP',
                    inputSchema={'type': 'object', 'properties': {}})
         for name in ('vivado_start', 'vivado_execute', 'vivado_stop')]


async def main():
    server = Server('fake-vivado-mcp')

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name, arguments):
        return [types.TextContent(type='text', text='{"status":"success"}')]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
