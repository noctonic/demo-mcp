"""
resource_folder.py

Provides a clean interface for monkey-patching MCP ResourceManager for
list_changed semantics and for tracking folder resources.
"""
import asyncio
import logging
from collections import defaultdict
from pprint import pformat

from mcp.server.fastmcp.resources import ResourceManager
from mcp.server.lowlevel.server import request_ctx
import mcp.server.lowlevel.server as low_server

# Debug: log every outgoing MCP notification
#logger = logging.getLogger("uvicorn.error")
# from mcp.shared.session import BaseSession
# orig_send_notification = BaseSession.send_notification
# async def _log_send_notification(self, notification):
#     logger.info(f"[MCP OUTGOING] method={getattr(notification, 'method', None)} params={getattr(notification, 'params', None)}")
#     return await orig_send_notification(self, notification)
# BaseSession.send_notification = _log_send_notification

logger = logging.getLogger("uvicorn.error")

# Per-URI subscriber map (for resource updated notifications)
subscribers = defaultdict(set)
# Global session set (for list_changed broadcasts)
all_sessions = set()

def install_patches(mcp):
    """Apply monkey patches to ResourceManager and MCP server handlers."""
    low_types = low_server.types

    # Introduce remove_resource method to ResourceManager (it did not exist)
    def remove_resource(self, uri: str):
        uri_str = str(uri)
        # Remove the resource from internal map
        if uri_str in self._resources:
            del self._resources[uri_str]
        # Broadcast list_changed to all sessions that requested list
        try:
            sess_list = [repr(s) for s in all_sessions]
            logger.info("[ListChanged] sessions=%s", pformat(sess_list))
        except Exception:
            logger.info("[ListChanged] sessions count=%d", len(all_sessions))
        for sess in list(all_sessions):
            asyncio.create_task(sess.send_resource_list_changed())
        # Clean up per-URI subscribers map
        subscribers.pop(uri_str, None)
    ResourceManager.remove_resource = remove_resource

    # Patch add_resource
    orig_add = ResourceManager.add_resource
    def add_resource(self, resource):
        orig_add(self, resource)
        # Broadcast list_changed
        try:
            sess_list = [repr(s) for s in all_sessions]
            logger.info("[ListChanged] sessions=%s", pformat(sess_list))
        except Exception:
            logger.info("[ListChanged] sessions count=%d", len(all_sessions))
        for sess in list(all_sessions):
            asyncio.create_task(sess.send_resource_list_changed())
    ResourceManager.add_resource = add_resource

    # Wrap list_resources to track sessions
    orig_list = mcp._mcp_server.request_handlers.get(low_types.ListResourcesRequest)
    async def _tracked_list(req):
        sess = request_ctx.get().session
        all_sessions.add(sess)
        logger.info(f"[Connect] resources/list session: {sess}")
        if orig_list:
            return await orig_list(req)
        return low_types.ListResourcesResult(resources=[])
    mcp._mcp_server.request_handlers[low_types.ListResourcesRequest] = _tracked_list

    # Subscribe/unsubscribe handlers for per-URI updates
    @mcp._mcp_server.subscribe_resource()
    async def _on_subscribe(uri: str):
        key = str(uri)
        sess = request_ctx.get().session
        subscribers[key].add(sess)
        logger.info(f"[Subscribe] {key} -> {list(subscribers[key])}")

    @mcp._mcp_server.unsubscribe_resource()
    async def _on_unsubscribe(uri: str):
        key = str(uri)
        sess = request_ctx.get().session
        subscribers[key].discard(sess)
        logger.info(f"[Unsubscribe] {key} -> {list(subscribers[key])}")

def setup_watcher(mcp, app, watch_dir):
    """Configure folder watcher into the server's startup lifecycle."""
    import os
    from pathlib import Path
    from mcp.server.fastmcp.resources import FunctionResource
    from pydantic.networks import AnyUrl

    async def watch_resources():
        watch_dir_path = Path(watch_dir)
        last_snap = {f: f.stat().st_mtime for f in watch_dir_path.iterdir() if f.is_file()}
        while True:
            await asyncio.sleep(5)
            current = {f: f.stat().st_mtime for f in watch_dir_path.iterdir() if f.is_file()}
            # Handle new files
            for f, mtime in current.items():
                uri = f"file://{f.resolve()}"
                if f not in last_snap:
                    logger.info(f"[Watcher] New resource: {uri}")
                    async def _read_file(path=f):
                        return path.read_text()
                    resource = FunctionResource(uri=AnyUrl(uri), fn=_read_file, mime_type="text/plain")
                    mcp._resource_manager.add_resource(resource)
                elif last_snap[f] != mtime:
                    logger.info(f"[Watcher] Modified resource: {uri}")
                    key = uri
                    for sess in subscribers.get(key, []):
                        asyncio.create_task(sess.send_resource_updated(uri))
            # Handle deletions
            for f in set(last_snap) - set(current):
                uri = f"file://{f.resolve()}"
                logger.info(f"[Watcher] Removed resource: {uri}")
                mcp._resource_manager.remove_resource(uri)
            last_snap = current

    @app.on_event('startup')
    async def _start_watcher():
        # Register existing files
        for f in Path(watch_dir).iterdir():
            if f.is_file():
                uri = f"file://{f.resolve()}"
                async def _read_file(path=f):
                    return path.read_text()
                resource = FunctionResource(uri=AnyUrl(uri), fn=_read_file, mime_type="text/plain")
                mcp._resource_manager.add_resource(resource)
        # Launch watcher
        asyncio.create_task(watch_resources())