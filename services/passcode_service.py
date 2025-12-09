# services/passcode_service.py
from __future__ import annotations
import hashlib
from typing import Optional, List, Dict

from db.db_conn import get_conn
from services.log_service import log_access
from services.settings_service import get_all_settings

# Optional vault (encryption) — keep working even if vault key is missing
try:
    from services.vault import enc as _vault_enc, dec as _vault_dec  # may raise if not configured at runtime
except Exception:
    _vault_enc = None
    _vault_dec = None

DEFAULT_MINUTES = 60
MAX_LEN = 4  # keypad-style: exactly 4 digits


# ------------------------- helpers -------------------------
def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()

def _mask(code: str) -> str:
    """
    By request, we keep 'masked' equal to the plain code so that UI can display guest codes as-is.
    If you want ****-1234 style later, change this to that format.
    """
    return code

def _enc_or_none(code: str):
    """Encrypt code if vault is available; otherwise return None (column is nullable)."""
    if _vault_enc is None:
        return None
    try:
        return _vault_enc(code)
    except Exception:
        return None

def _dec_or_empty(blob) -> str:
    """Decrypt blob if vault is available; otherwise return empty string."""
    if not blob or _vault_dec is None:
        return ""
    try:
        return _vault_dec(blob)
    except Exception:
        return ""


def _ensure_code_enc_column():
    """
    Make sure `code_enc` column exists (MySQL). Safe to call each time.
    """
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM passcodes LIKE 'code_enc'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE passcodes ADD COLUMN code_enc LONGBLOB NULL")
            cn.commit()


def _validate_numeric_code(code: str):
    """
    Passcode phải là CHÍNH XÁC MAX_LEN ký tự số.
    """
    if not code or not code.isdigit() or len(code) != MAX_LEN:
        raise ValueError(f"Passcode must be exactly {MAX_LEN} digits.")


# ------------------------- create / update -------------------------
def set_main_passcode(code: str) -> None:
    """
    Set main passcode:
      - code_hash: SHA-256
      - code_masked: (currently plain code for UI)
      - code_enc: encrypted if vault is configured, else NULL
    """
    _validate_numeric_code(code)
    _ensure_code_enc_column()
    h = _hash(code)
    masked = _mask(code)
    enc_blob = _enc_or_none(code)

    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("UPDATE passcodes SET is_main=0 WHERE is_main=1")
        cur.execute(
            """INSERT INTO passcodes(code_hash, code_masked, is_main, is_one_time, code_enc)
               VALUES (%s,%s,1,0,%s)""",
            (h, masked, enc_blob),
        )
        cn.commit()


def create_temp_passcode(code: str, minutes_valid: Optional[int] = None) -> None:
    _validate_numeric_code(code)
    _ensure_code_enc_column()
    minutes_valid = int(minutes_valid or DEFAULT_MINUTES)
    h = _hash(code)
    masked = _mask(code)
    enc_blob = _enc_or_none(code)

    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            """INSERT INTO passcodes(code_hash, code_masked, is_main, is_one_time, valid_until, code_enc)
               VALUES (%s,%s,0,0, DATE_ADD(NOW(), INTERVAL %s MINUTE), %s)""",
            (h, masked, minutes_valid, enc_blob),
        )
        cn.commit()


def create_one_time_passcode(code: str, minutes_valid: Optional[int] = None) -> None:
    _validate_numeric_code(code)
    _ensure_code_enc_column()
    minutes_valid = int(minutes_valid or DEFAULT_MINUTES)
    h = _hash(code)
    masked = _mask(code)
    enc_blob = _enc_or_none(code)

    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(
            """INSERT INTO passcodes(code_hash, code_masked, is_main, is_one_time, valid_until, code_enc)
               VALUES (%s,%s,0,1, DATE_ADD(NOW(), INTERVAL %s MINUTE), %s)""",
            (h, masked, minutes_valid, enc_blob),
        )
        cn.commit()


# ------------------------- check -------------------------
def check_passcode(code: str) -> bool:

    # == respect toggle ==
    try:
        s = get_all_settings or {}
        if not s.get('passcode_enable', 1):
            log_access('passcode', 'denied', passcode_masked=code)
            return False
    except Exception:
        pass

    """
    Return True if matches main OR an active, unused (if one-time) guest.
    Also logs the attempt to access_log.
    """
    _validate_numeric_code(code)
    h = _hash(code)
    masked = _mask(code)
    ok = False

    with get_conn() as cn, cn.cursor(dictionary=True) as cur:
        # main?
        cur.execute("SELECT id FROM passcodes WHERE is_main=1 AND code_hash=%s LIMIT 1", (h,))
        if cur.fetchone():
            ok = True
        else:
            # guest (must be within validity AND not used if one-time)
            cur.execute(
                """SELECT id, is_one_time, used
                   FROM passcodes
                   WHERE is_main=0 AND code_hash=%s
                     AND valid_until IS NOT NULL AND valid_until >= NOW()
                   LIMIT 1""",
                (h,),
            )
            row = cur.fetchone()
            if row and int(row["used"] or 0) == 0:
                ok = True
                # mark one-time as used
                if int(row["is_one_time"] or 0) == 1:
                    with get_conn() as cn2, cn2.cursor() as cur2:
                        cur2.execute("UPDATE passcodes SET used=1 WHERE id=%s", (row["id"],))
                        cn2.commit()

    # log the attempt
    log_access("passcode", "granted" if ok else "denied", passcode_masked=masked, passcode_hash=h)
    return ok


# ------------------------- status / list -------------------------
def has_main_passcode() -> bool:
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT 1 FROM passcodes WHERE is_main=1 LIMIT 1")
        return cur.fetchone() is not None


def list_active_guest_codes() -> List[Dict]:
    """
    Return active guest codes that still have a remaining validity (valid_until >= NOW()).
    NOTE: unlimited guest codes are intentionally excluded (by design).
    """
    with get_conn() as cn, cn.cursor(dictionary=True) as cur:
        cur.execute(
            """SELECT id, code_masked,
                      GREATEST(0, TIMESTAMPDIFF(SECOND, NOW(), valid_until)) AS remain_sec
               FROM passcodes
               WHERE is_main=0 AND used=0
                 AND valid_until IS NOT NULL AND valid_until >= NOW()
               ORDER BY valid_until ASC, id ASC"""
        )
        rows = cur.fetchall() or []
    return [
        {"id": r["id"], "code_masked": r["code_masked"], "remain_sec": int(r["remain_sec"])}
        for r in rows
    ]


# ------------------------- reveal / delete -------------------------
def reveal_main_passcode() -> str:
    """
    Decrypt and return the main passcode (if encrypted and key available).
    Return empty string if not available.
    """
    _ensure_code_enc_column()
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT code_enc FROM passcodes WHERE is_main=1 ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    return _dec_or_empty(row[0] if row else None)

def reveal_guest_passcode(passcode_id: int) -> str:
    _ensure_code_enc_column()
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT code_enc FROM passcodes WHERE id=%s AND is_main=0 LIMIT 1", (passcode_id,))
        row = cur.fetchone()
    return _dec_or_empty(row[0] if row else None)

def delete_guest_passcode(passcode_id: int) -> None:
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("DELETE FROM passcodes WHERE id=%s AND is_main=0", (passcode_id,))
        cn.commit()
