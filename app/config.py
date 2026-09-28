"""All configuration comes from environment variables (loaded from .env if present)."""
import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# How much the model thinks: minimal | low | medium | high. Lower = cheaper and faster.
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "medium")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://agent:agent@localhost:5433/research_agent"
)
# Hosts like Render give "postgres://" or "postgresql://" URLs; SQLAlchemy needs the driver name (psycopg).
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(prefix):]

# Guardrails: stop the agent after this many model calls or this many seconds.
MAX_STEPS = int(os.getenv("MAX_STEPS", "8"))
RUN_TIMEOUT_SECONDS = int(os.getenv("RUN_TIMEOUT_SECONDS", "180"))
