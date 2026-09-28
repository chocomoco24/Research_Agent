"""FastAPI application entry point. Run with: uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import models  # noqa: F401  (import registers the tables on Base)
from app.db.database import Base, engine
from app.routes import runs

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup if they don't exist (simple alternative to migrations).
    Base.metadata.create_all(bind=engine)
    yield


STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Research Agent", version="0.1.0", lifespan=lifespan)
app.include_router(runs.router)
# The frontend (plain HTML/CSS/JS) is served by the same server, so no CORS setup is needed.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
