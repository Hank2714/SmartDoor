# services/fingerprint_service.py
from __future__ import annotations
from typing import List, Dict, Optional, Callable
import time
from collections import deque

from db.db_conn import get_conn
from .serial_service import SerialService

# =========================
#  DB HELPERS (GIỮ NGUYÊN)
# =========================
def list_fingerprints() -> List[Dict]:
    sql = "SELECT id, name FROM fingerprint_data ORDER BY id DESC"
    with get_conn() as cn, cn.cursor(dictionary=True) as cur:
        cur.execute(sql)
        rows = cur.fetchall() or []
    return rows

def add_fingerprint_placeholder(name: str = "") -> int:
    """
    Placeholder để test UI. Khi tích hợp ESP32:
    - Có thể sửa lại để lưu template thật nếu bạn đọc được từ ESP.
    """
    sql = "INSERT INTO fingerprint_data(name, template) VALUES (%s, %s)"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(sql, (name, b""))
        cn.commit()
        return cur.lastrowid

def delete_fingerprint(fid: int) -> None:
    sql = "DELETE FROM fingerprint_data WHERE id = %s"
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute(sql, (fid,))
        cn.commit()


# =====================================
#  ESP32 FINGERPRINT SERIAL CONTROLLER
# =====================================
class ESPFingerprint:
    """
    Giao tiếp với 'main.py' trên ESP32 (cảm biến vân tay).
    Các lệnh hỗ trợ (đúng theo code bạn gửi):
      - 'enroll'
      - 'delete all'
      - 'delete <id>'
      - 'library' (trả về slot trống đầu tiên)
    """

    def __init__(self, on_line: Optional[Callable[[str], None]] = None, timeout_s: float = 8.0):
        self._lines = deque(maxlen=200)
        self._timeout = float(timeout_s)
        self._user_cb = on_line
        # Tự dò cổng theo .env giống phần cửa (shared 1 ESP32)
        self._serial = SerialService(on_message=self._rx_line)

    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.available)

    # ---------- High-level ops ----------
    def enroll(self) -> tuple[bool, Optional[int], str]:
        """
        Bắt đầu enroll trên cảm biến.
        Trả về: (ok, page_id, message)
        - ok=True nếu thấy chuỗi 'Inform enroll complete, ID:<n>'
        """
        if not self.is_connected():
            return False, None, "Serial not connected"

        self._drain()
        self._send("enroll")
        # Chờ đến khi thấy success/failed/timeout
        deadline = time.time() + self._timeout
        page_id = None
        msg = "timeout"

        while time.time() < deadline:
            line = self._pop(0.1)
            if not line:
                continue
            if self._user_cb:
                self._user_cb(line)

            # Thành công
            if line.lower().startswith("inform enroll complete, id:"):
                try:
                    page_id = int(line.split(":")[-1].strip())
                except Exception:
                    page_id = None
                return True, page_id, "enroll complete"

            # Lỗi thường gặp
            if line.lower().startswith("error enroll"):
                msg = line
                break

        return False, page_id, msg

    def delete(self, page_id: int) -> tuple[bool, str]:
        """
        Xoá 1 mẫu theo ID.
        Trả về: (ok, message)
        """
        if not self.is_connected():
            return False, "Serial not connected"

        self._drain()
        self._send(f"delete {int(page_id)}")
        deadline = time.time() + 3.0
        msg = "timeout"

        while time.time() < deadline:
            line = self._pop(0.2)
            if not line:
                continue
            if self._user_cb:
                self._user_cb(line)

            if line.lower().startswith("inform delete success"):
                return True, "deleted"
            if line.lower().startswith("error delete"):
                return False, line

        return False, msg

    def delete_all(self) -> tuple[bool, str]:
        if not self.is_connected():
            return False, "Serial not connected"

        self._drain()
        self._send("delete all")
        deadline = time.time() + 8.0
        msg = "timeout"

        while time.time() < deadline:
            line = self._pop(0.2)
            if not line:
                continue
            if self._user_cb:
                self._user_cb(line)

            if line.lower().startswith("inform delete success"):
                return True, "all deleted"
            if line.lower().startswith("error delete"):
                return False, line

        return False, msg

    def library_first_empty(self) -> tuple[bool, Optional[int], str]:
        """
        Hỏi slot trống đầu tiên.
        Trả về: (ok, slot, message)
        """
        if not self.is_connected():
            return False, None, "Serial not connected"

        self._drain()
        self._send("library")
        deadline = time.time() + 3.0
        msg = "timeout"
        slot = None

        while time.time() < deadline:
            line = self._pop(0.2)
            if not line:
                continue
            if self._user_cb:
                self._user_cb(line)

            if line.lower().startswith("inform library first empty slot:"):
                try:
                    slot = int(line.split(":")[-1].strip())
                except Exception:
                    slot = None
                return True, slot, "ok"
            if line.lower().startswith("error library"):
                return False, None, line

        return False, slot, msg

    # ---------- internal ----------
    def _send(self, line: str) -> None:
        try:
            if self._serial and self._serial.available:
                self._serial.send(line)
        except Exception:
            pass

    def _rx_line(self, line: str) -> None:
        # Bỏ qua dòng LED nếu firmware còn in
        if not line or "LED set success" in line:
            return
        self._lines.append(line)
        if self._user_cb:
            try:
                self._user_cb(line)
            except Exception:
                pass

    def _pop(self, wait_s: float) -> Optional[str]:
        deadline = time.time() + max(0.0, wait_s)
        while time.time() < deadline:
            if self._lines:
                return self._lines.popleft()
            time.sleep(0.02)
        return self._lines.popleft() if self._lines else None

    def _drain(self) -> None:
        try:
            self._lines.clear()
        except Exception:
            pass
