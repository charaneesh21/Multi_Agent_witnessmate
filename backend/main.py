"""backend/main.py
FastAPI application entry point.

Run with:
    uvicorn backend.main:app --reload --port 8000

Portals (served as static sites):
    http://localhost:8000/employee/   →  Employee login + dashboard
    http://localhost:8000/admin/        →  Admin dashboard

API:
    http://localhost:8000/api/auth/*
    http://localhost:8000/api/employee/*
    http://localhost:8000/api/admin/*
    http://localhost:8000/docs        →  Swagger UI
"""

from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from backend.database.connection import init_db
from backend.middleware.ai_gateway import AIGatewayMiddleware
from backend.routers import auth as auth_router
from backend.routers import admin as admin_router
from backend.routers import employee as employee_router
from config.settings import get_settings

_settings = get_settings()
_FRONTEND = Path(__file__).parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Multi-Agent System",
    description="Employee portal + CEO intelligence dashboard backed by Groq agents.",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)


# ---------------------------------------------------------------------------
# Swagger Bearer Auth button
# ---------------------------------------------------------------------------
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
        }
    }
    for path in schema["paths"].values():
        for method in path.values():
            method["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# AI Gateway middleware
# ---------------------------------------------------------------------------
def _load_config() -> dict:
    with open(_settings.active_template_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


app.add_middleware(AIGatewayMiddleware, config=_load_config())


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
app.include_router(auth_router.router)
app.include_router(employee_router.router)
app.include_router(admin_router.router)


# ---------------------------------------------------------------------------
# Static frontends
# ---------------------------------------------------------------------------

app.mount(
    "/employee/register",
    StaticFiles(directory=str(_FRONTEND / "employee" / "register"), html=True),
    name="employee-register",
)
app.mount(
    "/employee",
    StaticFiles(directory=str(_FRONTEND / "employee"), html=True),
    name="employee-portal",
)
app.mount(
    "/admin",
    StaticFiles(directory=str(_FRONTEND / "admin"), html=True),
    name="admin-dashboard",
)

app.mount(
    "/register",
    StaticFiles(directory=str(_FRONTEND / "register"), html=True),
    name="register",
)


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/", tags=["meta"])
async def root():
    return {
        "employee_portal": "/employee/",
        "admin_dashboard": "/admin/",
        "register": "/register/",
        "api_docs": "/docs",
    }
    
