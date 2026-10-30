"""Vercel entry point: every path goes to live/server.py's App (see vercel.json).

On Vercel, each request carries the project's OIDC token, which is the service token AssemblyAI's key
exchange checks, the same way the AssemblyAI MCP server proves itself. Set LIVE_MODE=oauth,
LIVE_PUBLIC_URL, LIVE_SESSION_SECRET and ASSEMBLYAI_OAUTH_CLIENT_ID in the project's settings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

# Not named "app": Vercel's Python runtime treats a module-level "app" as a WSGI/ASGI callable and fails every
# request with "Could not determine the application interface". It serves the "handler" class below instead.
live_app = server.App.from_env()
if live_app.mode != "oauth":
    raise SystemExit("Deploy with LIVE_MODE=oauth: dev mode spends one API key and only serves localhost.")


class handler(server.Handler):
    pass


handler.app = live_app
