"""
Waffler Backend API
FastAPI application for account management, usage tracking, and billing
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import routers
from app.auth.router import router as auth_router
from app.usage.router import router as usage_router
from app.subscription.router import router as subscription_router
from app.style.router import router as style_router

# Create FastAPI app
app = FastAPI(
    title="Waffler API",
    description="Account management and usage tracking for Waffler",
    version="1.0.0"
)

# CORS middleware - allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(usage_router, prefix="/usage", tags=["Usage"])
app.include_router(subscription_router, prefix="/subscription", tags=["Subscription"])
app.include_router(style_router, prefix="/style", tags=["LLM Styling"])


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Waffler API",
        "version": "1.0.0",
        "status": "healthy"
    }


@app.get("/health")
async def health():
    """Health check for monitoring"""
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
