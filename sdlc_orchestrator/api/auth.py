"""sdlc_orchestrator/api/auth.py
Optional API-key authentication.
Set AISDLC_API_KEY env var to enable; if unset all requests pass through.
"""
import os
from fastapi import HTTPException, Request, status


_KEY = os.environ.get("AISDLC_API_KEY", "")


def verify_request(request: Request) -> None:
    """FastAPI dependency — enforces Bearer token when AISDLC_API_KEY is set."""
    if not _KEY:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    if auth[len("Bearer "):] != _KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def verify_ws_token(token: str) -> None:
    """Called from WebSocket handler with ?token= query param."""
    if not _KEY:
        return
    if token != _KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid WebSocket token")
