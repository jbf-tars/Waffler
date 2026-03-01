"""
App Secret Middleware
Validates X-App-Secret header on proxy endpoints to prevent unauthorized API usage.
"""

import os
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

APP_SECRET = os.getenv("APP_SECRET", "")

# Paths that don't require the app secret (health checks, auth endpoints)
PUBLIC_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
PUBLIC_PREFIXES = ("/auth/",)


class AppSecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for public endpoints
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # If APP_SECRET is not configured, skip check (local dev)
        if not APP_SECRET:
            return await call_next(request)

        # Validate the header
        provided = request.headers.get("X-App-Secret", "")
        if provided != APP_SECRET:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing app secret"
            )

        return await call_next(request)
