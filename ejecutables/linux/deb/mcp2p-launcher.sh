#!/bin/bash
export WEBKIT_DISABLE_DMABUF_RENDERER=1
APP_DIR="/opt/mcp2p"
cd "$APP_DIR" || exit 1
exec "$APP_DIR/Mcp2p" "$@"
