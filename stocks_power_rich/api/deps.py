import sqlite3
from ..config import load_config
from ..db import get_connection, init_db

_db_initialized = set()

def conn():
    cfg = load_config()
    c = get_connection(cfg.db_path)
    if cfg.db_path not in _db_initialized:
        init_db(c)
        _db_initialized.add(cfg.db_path)
    return c
