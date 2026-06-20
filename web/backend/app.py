"""Mycelium Web Backend — FastAPI application."""

from __future__ import annotations

import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Add scripts/ to path so v3 modules can be imported
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

app = FastAPI()

# Static file serving
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
if FRONTEND_SRC.exists():
    app.mount("/src", StaticFiles(directory=FRONTEND_SRC / "src"), name="src")

# Include route modules — catch-all must come LAST
from .routes_core import router as core_router
from .routes_memory import router as memory_router
from .routes_reader import router as reader_router
from .routes_prompts import router as prompts_router
from .routes_artifacts import router as artifacts_router
from .routes_fallback import router as fallback_router

app.include_router(core_router)
app.include_router(memory_router)
app.include_router(reader_router)
app.include_router(prompts_router)
app.include_router(artifacts_router)
app.include_router(fallback_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
