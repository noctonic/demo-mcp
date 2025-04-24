"""
server.py – MCP SSE Server

Demonstrates MCP tools, prompts, resources, and resource templates

"""

import argparse
import asyncio
import logging
import uvicorn
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.lowlevel.server import NotificationOptions

import resource_folder #folder watching demo
from pydantic.networks import AnyUrl
import mcp.types as _mcp_types

import uvicorn
from mcp.types import ListRootsRequest, ListRootsResult, Root, ServerResult

mcp = FastMCP(
    name="demo-mcp-sse-server",
    version="0.1.0",
    instructions="Demo MCP SSE server using high-level FastMCP API",
)
# In-memory store of roots
server_roots: list[dict[str, str]] = []

@mcp.tool()
async def generate_poem(topic: str, context: Context) -> str:
    """Generate a short poem about the given topic."""
    # The server requests a completion from the client LLM via MCP sampling
    # Build sampling parameters
    params = {
        "messages": [
            {"role": "user", "content": {"type": "text", "text": f"Write a short poem about {topic}"}}
        ],
        "systemPrompt": "You are a talented poet who writes concise, evocative verses.",
        "maxTokens": 100
    }

    from mcp.types import CreateMessageRequest, CreateMessageResult, TextContent
    req = CreateMessageRequest(method="sampling/createMessage", params=params)
    res: CreateMessageResult = await context.session.send_request(req, CreateMessageResult)
    # Extract and return the generated text
    content = res.content
    if isinstance(content, TextContent):
        return content.text
    # Fallback to string conversion
    return str(content)


@mcp.tool()
async def post_message(ctx: Context, user_id: int, subject: str, body: str) -> str:
    """POST a message and return the created record."""
    data = "test-data"
    await ctx.report_progress(1, 1)
    ctx.info(f"Posted message id={data} for user {user_id}")
    print("USED post message",flush=True)
    return data

@mcp.prompt()
def git_commit(changes: str) -> str:
    """Generate a concise commit message for provided changes."""
    print("USED GIT COMMIT",flush=True)
    return (
        "Generate a concise but descriptive commit message for these changes:\n\n"
        + changes
    )

@mcp.resource("file:///logs/app.log")
async def read_log() -> str:
    print("USED READ_LOG",flush=True)
    """Return contents of the application log."""
    return "(log file empty)"

@mcp.resource("schema://{table}")
async def get_schema(table: str) -> dict:
    print("USED schema://","table",flush=True)
    """Return schema for the specified table."""
    return "(ID[int],FIRST_NAME[string],LAST_NAME[string]])"


# Progress Demonstration Tool
@mcp.tool()
async def long_task(ctx: Context, duration: int) -> str:
    print("USED LONG TASK",flush=True)
    """Simulate a long-running task, reporting progress over 'duration' seconds."""
    request_id = ctx.request_id
    total = duration
    for i in range(total):
        await asyncio.sleep(1)
        # Only report progress if client requested
        await ctx.report_progress(i + 1, total)
    return f"Completed long task of duration {total} seconds"
    
@mcp.tool()
async def echo_roots(ctx: Context) -> str:
    """Request the client's roots list and return as JSON."""
    # Ask client for current roots
    roots_result = await ctx.session.list_roots()
    # roots_result.roots is a list of Root objects with .uri and optional .name
    roots_list = [ { 'name': r.name, 'uri': str(r.uri) } for r in roots_result.roots ]
    import json
    return json.dumps(roots_list)


# Advertise Capabilities
orig_create_opts = mcp._mcp_server.create_initialization_options
def _create_opts_with_experimental(notification_options=None, experimental_capabilities=None):
    # Enable change notifications for prompts, resources, and tools
    opts = NotificationOptions(
        prompts_changed=True,
        resources_changed=True,
        tools_changed=True
    )

    # Create base initialization options
    init_opts = orig_create_opts(opts, {})
    # Allow clients to subscribe to resource changes
    init_opts.capabilities.resources.subscribe = True
    # Advertise roots list change capability
    if not hasattr(init_opts.capabilities, 'roots') or init_opts.capabilities.roots is None:
        init_opts.capabilities.roots = {}
    init_opts.capabilities.roots['listChanged'] = True
    return init_opts

mcp._mcp_server.create_initialization_options = _create_opts_with_experimental

# Register handler for client roots/list requests (MCP RPC)
async def _handle_list_roots(req: ListRootsRequest) -> ServerResult:
    # Return the current server_roots list as MCP Root objects
    root_objs = [Root(uri=r['uri'], name=r.get('name')) for r in server_roots]
    return ServerResult(ListRootsResult(roots=root_objs))
# Attach low-level request handler
mcp._mcp_server.request_handlers[ListRootsRequest] = _handle_list_roots


# Create the Starlette SSE app
app = mcp.sse_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MCP SSE server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--watch-dir", dest="watch_dir", default=None,
        help="Directory to watch for dynamic resources"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode and live reload")
    args = parser.parse_args()

    mcp.settings.debug = args.debug

    # Run Demo folder watching
    if args.watch_dir:
        watch_dir = Path(args.watch_dir)
        resource_folder.install_patches(mcp) 
        resource_folder.setup_watcher(mcp, app, watch_dir)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="debug" if args.debug else "info",
    )