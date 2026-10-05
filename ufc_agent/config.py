import os

from dotenv import load_dotenv

load_dotenv()


def require_env(*names):
    """Return the first non-empty env var among names, or raise naming all of them."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    raise RuntimeError(f"Missing environment variable: set {' or '.join(names)} in .env")


def supabase_db_url():
    url = require_env("SUPABASE_DB_URL")
    if not url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("SUPABASE_DB_URL must start with postgres:// or postgresql://")
    return url

