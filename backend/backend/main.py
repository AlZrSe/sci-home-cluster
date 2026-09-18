"""
Main entry point for the Scientific Home Cluster Backend API.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.core.config import settings
from backend.api.v1 import auth, jobs, nodes

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Set up CORS middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include API routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["auth"])
app.include_router(jobs.router, prefix=f"{settings.API_V1_STR}/jobs", tags=["jobs"])
app.include_router(nodes.router, prefix=f"{settings.API_V1_STR}/nodes", tags=["nodes"])


@app.get("/")
async def root():
    return {"message": "Welcome to Scientific Home Cluster API"}


@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    return {"status": "healthy", "version": settings.VERSION}