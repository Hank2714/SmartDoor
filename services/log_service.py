# services/log_service.py
from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime
import math

from db.db_conn import get_conn


def log_access(
    method: str,
    result: str,
    passcode_masked: Optional[str] = None,
    passcode_hash: Optional[str] = None,
    confidence: Optional[float] = None
) -> None:
    if confidence is not None:
        try:
            c = float(confidence)
            if math.isnan(c) or math.isinf(c):
                confidence = None
            else:
                confidence = c
        except Exception:
            confidence = None

    sql = """
        INSERT INTO access_log (method, result, passcode_masked, passcode_hash, confidence)
        VALUES (%s, %s, %s, %s, %s)
    """
    try:
        with get_conn() as cn, cn.cursor() as cur:
            cur.execute(sql, (method, result, passcode_masked, passcode_hash, confidence))
            cn.commit()
    except Exception as e:
        print(f"[log_access] Error logging access: {e}")
# ------------------------- recent openings for UI -------------------------
def get_recent_openings(limit: int = 20) -> list[dict]:
    """
    Lấy các lần MỞ CỬA gần nhất (result='granted') để hiển thị mini-log:
      - ts: thời điểm (alias của cột `timestamp` trong bảng access_log)
      - method
      - result
    """
    sql = """
        SELECT `timestamp` AS ts, method, result
        FROM access_log
        WHERE result = 'granted'
        ORDER BY `timestamp` DESC
        LIMIT %s
    """
    rows: list[dict] = []
    try:
        with get_conn() as cn, cn.cursor(dictionary=True) as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall() or []
    except Exception as e:
        print(f"[get_recent_openings] Error: {e}")
    return rows
# ------------------------- list logs by month -------------------------
def list_logs_by_month(year: int, month: int) -> list[dict]:
    """
    Lấy log theo tháng/năm để hiển thị ở Manage tab.
    """
    sql = """
        SELECT id, method, result, passcode_masked, `timestamp`
        FROM access_log
        WHERE YEAR(`timestamp`)=%s AND MONTH(`timestamp`)=%s
        ORDER BY `timestamp` DESC, id DESC
    """
    try:
        with get_conn() as cn, cn.cursor(dictionary=True) as cur:
            cur.execute(sql, (year, month))
            return cur.fetchall() or []
    except Exception as e:
        print(f"[list_logs_by_month] Error: {e}")
        return []

def clear_logs(year: int, month: int) -> None:
    sql = "DELETE FROM access_log WHERE YEAR(`timestamp`)=%s AND MONTH(`timestamp`)=%s"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(sql, (year, month))
        cn.commit()

def delete_log(log_id: int) -> None:
    sql = "DELETE FROM access_log WHERE id=%s"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(sql, (log_id,))
        cn.commit()
