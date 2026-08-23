import os
import sys
import sqlite3

# Vercel serverless functions can only write persistently to external storage;
# /tmp is the writable ephemeral filesystem available at runtime.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_real_connect = sqlite3.connect


def _vercel_connect(database, *args, **kwargs):
    if isinstance(database, str) and database.endswith("nicheradar.db"):
        database = "/tmp/nicheradar.db"
    return _real_connect(database, *args, **kwargs)


sqlite3.connect = _vercel_connect

from app import app  # noqa: E402,F401
