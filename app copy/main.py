"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

from app.config import get_settings
from app.core.auth import get_current_user_optional
from app.database import init_db
from app.api.routes import admin, auth, events, pages, users

settings = get_settings()

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle.

    Yields:
        None: During normal operation.
    """
    # Startup event
    print(f"Starting {settings.app_name} v{settings.app_version} ({settings.environment})")
    init_db()
    print("Database initialized")

    yield

    # Shutdown event
    print(f"Shutting down {settings.app_name}")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    description="Quality Event Management System for Manufacturing SMBs",
    version=settings.app_version,
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Mount static files if directory exists
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(pages.router)
app.include_router(users.router)


# Health check endpoint
@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint.

    Returns:
        dict: Health status.
    """
    return {
        "status": "healthy",
        "version": settings.app_version,
        "environment": settings.environment,
    }


# Root endpoint
@app.get("/")
async def root(
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    """Render the public landing page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_user": current_user,
            "app_name": settings.app_name,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
