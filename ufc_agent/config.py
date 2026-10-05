import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def require_env(*names):
    """Return the first non-empty env var among names, or raise naming all of them."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    raise RuntimeError(f"Missing environment variable: set {' or '.join(names)} in .env")


def optional_env(name, default=None):
    value = (os.getenv(name) or "").strip()
    return value or default


def supabase_db_url():
    url = require_env("SUPABASE_DB_URL")
    if not url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("SUPABASE_DB_URL must start with postgres:// or postgresql://")
    return url


CACHE_DIR = Path(optional_env("UFC_AGENT_CACHE_DIR", str(PROJECT_ROOT / "data" / "cache")))
