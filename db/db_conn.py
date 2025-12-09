import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling

load_dotenv()

def _pool():
    return pooling.MySQLConnectionPool(
        pool_name="smartdoor_pool",
        pool_size=5,
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "smartdoor_db"),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
    )

_POOL = None

def get_conn():
    global _POOL
    if _POOL is None:
        _POOL = _pool()
    return _POOL.get_connection()
