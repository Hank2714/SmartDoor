from db.db_conn import get_conn

SETTINGS_ID = 1

def ensure_settings_row():
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO settings(id) VALUES (1)")
        cn.commit()

def get_all_settings():
    ensure_settings_row()
    with get_conn() as cn, cn.cursor(dictionary=True) as cur:
        cur.execute("SELECT * FROM settings WHERE id=%s", (SETTINGS_ID,))
        return cur.fetchone()

def update_hold_time(seconds: int):
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("UPDATE settings SET hold_time=%s WHERE id=%s", (seconds, SETTINGS_ID))
        cn.commit()

def set_toggle(name: str, enabled: bool):
    assert name in ("face_recognition_enabled", "fingerprint_enabled", "passcode_enabled")
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(f"UPDATE settings SET {name}=%s WHERE id=%s", (1 if enabled else 0, SETTINGS_ID))
        cn.commit()

def set_door_state(state: str):
    # 'open' | 'close'
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("UPDATE settings SET door_state=%s WHERE id=%s", (state, SETTINGS_ID))
        cn.commit()
