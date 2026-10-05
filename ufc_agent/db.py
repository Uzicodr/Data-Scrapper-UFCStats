import psycopg
from psycopg.rows import dict_row

from ufc_agent.config import supabase_db_url


def connect():
    # prepare_threshold=None: Supabase's transaction pooler (port 6543) does not
    # support server-side prepared statements.
    return psycopg.connect(supabase_db_url(), row_factory=dict_row, prepare_threshold=None)
