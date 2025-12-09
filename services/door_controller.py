# services/door_controller.py
from __future__ import annotations
import time
import re
import threading
from typing import Callable, Optional, List

from .serial_service import SerialService


class DoorController:
    """
    Điều khiển cửa qua ESP32 bằng text command trên USB serial.

    ESP32 main.py đang xử lý các lệnh:
      - "open manual"    -> open_door()            -> In: Inform door opening manual / Inform door opened
      - "open face"      -> open_door()            -> In: Inform door opening face   / Inform door opened
      - "open passcode"  -> open_door()            -> In: Inform door opening passcode / Inform door opened
      - "close"          -> close_door()           -> In: Inform door closing / Inform door closed
      - "enroll", "delete all", "library", "led", "end" ...

    Ở phía PC, controller sẽ:
      - Gửi lệnh mở/đóng cho ESP32 (open manual/face/passcode, close).
      - Lắng nghe dòng từ ESP32, tự động:
          + Nhận passcode từ keypad: "Inform passcode 1234"
            -> Kiểm tra với main/guest passcode trong DB
            -> Nếu hợp lệ & passcode_enabled = 1 -> gửi "open passcode"
          + Nhận sự kiện vân tay: "Inform finger found, ID:x" / "Inform finger not found"
            -> Ghi log access fingerprint (ESP32 tự mở cửa, PC chỉ log)
          + Nhận "Inform door opened"
            -> Sau hold_time giây tự gửi "close" (auto-close).

    API public cho UI:
      - is_connected()
      - open_door()         # mở manual
      - close_door()
      - open_ms(ms)         # giữ tương thích code cũ (map sang open_door)
      - close()             # alias close_door
      - shutdown()
      - add_listener(cb)    # nhận line raw từ ESP32
      - send_raw(cmd)       # gửi command text tuỳ ý (vd: "enroll", "delete all")
      - set_hold_time(sec)  # cập nhật hold_time runtime
    """

    def __init__(self, on_event: Optional[Callable[[str], None]] = None):
        self._on_event = on_event
        self._serial = SerialService(on_message=self._handle_rx)
        self._listeners: List[Callable[[str], None]] = []

        # --- Auto close settings ---
        self._hold_time_sec: int = 5  # default 5s
        self._auto_close_timer: Optional[threading.Timer] = None
        self._load_initial_settings()

    # ---------- Settings ----------
    def _load_initial_settings(self) -> None:
        """
        Thử load hold_time từ settings_service (nếu có):
          - door_hold_time_sec
          - hoặc hold_time_sec
        Nếu lỗi / không có -> giữ default.
        """
        try:
            from .settings_service import get_all_settings  # type: ignore
        except Exception:
            return

        try:
            s = get_all_settings() or {}
            v = s.get("door_hold_time_sec", s.get("hold_time_sec", None))
            if v is not None:
                self._hold_time_sec = max(0, int(v))
        except Exception:
            pass

    def set_hold_time(self, seconds: int) -> None:
        """
        Cho phép UI / settings tab cập nhật hold_time khi người dùng đổi.
        (Phần ghi xuống DB bạn xử lý ở chỗ khác).
        """
        try:
            sec = int(seconds)
        except Exception:
            sec = 0
        self._hold_time_sec = max(0, sec)

    # ---------- Listener management ----------
    def add_listener(self, cb: Callable[[str], None]) -> None:
        """Đăng ký callback nhận từng dòng text từ ESP32."""
        if cb not in self._listeners:
            self._listeners.append(cb)

    # ---------- Basic API ----------
    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.available)

    def _send(self, line: str):
        if not self._serial or not self._serial.available:
            return
        try:
            self._serial.send(line)
        except Exception:
            pass

    # --- Manual open/close, bind với UI ---
    def open_door(self):
        """
        Mở cửa theo kênh 'manual':
          PC  -> "open manual"
          ESP -> Inform door opening manual / Inform door opened
        """
        self._send("open manual")

    def close_door(self):
        """
        Đóng cửa:
          PC  -> "close"
          ESP -> Inform door closing / Inform door closed
        """
        self._send("close")

    # ---------- Backward-compatible API ----------
    def open_ms(self, ms: int):
        """
        Giữ cho code cũ app.py không bị lỗi:
        ESP32 dùng limit switch, không dùng ms -> map sang open_door().
        """
        _ = ms  # không dùng, nhưng giữ tham số cho compatibility
        self.open_door()

    def close(self):
        """Alias cho close_door() để tương thích code cũ."""
        self.close_door()

    def shutdown(self):
        """Đóng kết nối serial khi thoát app."""
        try:
            if self._auto_close_timer:
                self._auto_close_timer.cancel()
        except Exception:
            pass
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass

    # ---------- Advanced / future ----------
    def open_manual(self):
        """Alias semantic cho open_door(), dùng cho nút 'Open door'."""
        self.open_door()

    def send_raw(self, cmd: str):
        """Cho debug: gửi thẳng 1 lệnh text xuống ESP32 (vd: 'enroll', 'delete all')."""
        self._send(cmd)

    # ---------- RX handler (core logic PC) ----------
    def _handle_rx(self, line: str):
        """
        Nhận 1 dòng text từ ESP32 (stdout của main.py).

        Luồng xử lý:
          1) _process_line_for_logic(line)  -> PC tự động xử lý passcode, fingerprint, hold_time, logging...
          2) on_event (callback chính, thường là App.on_serial_event)
          3) mọi listeners đã add_listener (vd: HomeTab._on_serial_line)
        """
        if not line:
            return

        # 1) PC xử lý logic nội bộ (passcode, fingerprint, auto-close, logging...)
        try:
            self._process_line_for_logic(line)
        except Exception:
            # Không để lỗi internal làm rơi mất log gốc cho UI
            pass

        # 2) callback chính
        if self._on_event:
            try:
                self._on_event(line)
            except Exception:
                pass

        # 3) các listener phụ (UI tabs, logger, ...)
        for cb in list(self._listeners):
            try:
                cb(line)
            except Exception:
                pass

    # ---------- Internal logic: parse các dòng "Inform ..." ----------
    def _process_line_for_logic(self, line: str) -> None:
        """
        Phân tích các dòng từ ESP32 để:
          - Nhận passcode keypad:  "Inform passcode 1234"
          - Nhận trạng thái vân tay: "Inform finger found, ID:x" / "Inform finger not found"
          - Nhận trạng thái cửa: "Inform door opened" / "Inform door closing/closed"
        """
        text = line.strip()

        # 1) PASSCODE từ keypad
        # Hỗ trợ cả:
        #   "Inform passcode 1234"
        #   "Inform passcode: 1234"
        if text.startswith("Inform passcode"):
            msg = text[len("Inform passcode"):].strip()
            if msg.startswith(":"):
                msg = msg[1:].strip()
            code = msg
            if code:
                self._handle_passcode_from_keypad(code)
            return

        # 2) FINGERPRINT events
        if text.startswith("Inform finger found"):
            m = re.search(r"ID[: ]+(\d+)", text)
            finger_id = m.group(1) if m else None
            self._log_fingerprint(granted=True, finger_id=finger_id)
            # ESP32 tự open_door() trong path này, PC KHÔNG gửi lệnh open.
            return

        if "Inform finger not found" in text:
            self._log_fingerprint(granted=False, finger_id=None)
            return

        # 3) DOOR events -> auto-close quản lý bằng hold_time
        if "Inform door opened" in text:
            self._schedule_auto_close()
            return

        if "Inform door closing" in text or "Inform door closed" in text:
            self._cancel_auto_close()
            return

        # Các dòng khác (keypad key, enroll, library...) để UI tự xử lý nếu cần.

    # ---------- Auto-close helpers ----------
    def _schedule_auto_close(self) -> None:
        """
        Sau khi cửa mở xong (ESP báo "Inform door opened"),
        nếu hold_time > 0 thì set timer gửi lệnh "close".
        """
        if self._hold_time_sec <= 0:
            return

        # Huỷ timer cũ nếu có
        self._cancel_auto_close()

        def _task():
            try:
                self.close_door()
            except Exception:
                pass

        t = threading.Timer(self._hold_time_sec, _task)
        t.daemon = True
        self._auto_close_timer = t
        t.start()

    def _cancel_auto_close(self) -> None:
        """Huỷ timer auto-close (khi người dùng đóng cửa trước, hoặc mở lại...)."""
        if self._auto_close_timer is not None:
            try:
                self._auto_close_timer.cancel()
            except Exception:
                pass
            self._auto_close_timer = None

    # ---------- Passcode handling on PC ----------
    def _handle_passcode_from_keypad(self, code: str) -> None:
        """
        Xử lý khi ESP32 gửi lên:
            Inform passcode 123456
        Logic:
          1) Kiểm tra setting passcode_enabled từ DB (settings_service).
          2) So sánh với main passcode (reveal_main_passcode()).
          3) So sánh với tất cả guest codes đang active (list_active_guest_codes + reveal_guest_passcode).
          4) Nếu hợp lệ:
                 - Gửi lệnh "open passcode" xuống ESP32 (cửa mở bằng passcode).
                 - Ghi log_access(method="passcode", result="granted").
             Ngược lại:
                 - Ghi log_access(method="passcode", result="denied").
        """
        code = (code or "").strip()
        if not code:
            return

        # --- 1) Kiểm tra toggle passcode_enabled ---
        pass_enabled = True
        try:
            from .settings_service import get_all_settings  # type: ignore
            s = get_all_settings() or {}
            pass_enabled = bool(s.get("passcode_enabled", 1))
        except Exception:
            pass_enabled = True

        # Lazy import log_access để tránh vòng import
        try:
            from .log_service import log_access  # type: ignore
        except Exception:
            log_access = None  # type: ignore

        if not pass_enabled:
            if log_access:
                try:
                    log_access(method="passcode", result="blocked")
                except Exception:
                    pass
            return

        # --- 2) Check main & guest passcode ---
        ok = False

        try:
            from .passcode_service import (
                reveal_main_passcode,
                list_active_guest_codes,
                reveal_guest_passcode,
            )

            # Main passcode
            try:
                plain_main = reveal_main_passcode()
            except Exception:
                plain_main = None
            if plain_main and code == str(plain_main):
                ok = True
            else:
                # Guest codes
                rows = list_active_guest_codes() or []
                for r in rows:
                    pid = r.get("id")
                    if pid is None:
                        continue
                    try:
                        plain_guest = reveal_guest_passcode(int(pid))
                    except Exception:
                        plain_guest = None
                    if plain_guest and code == str(plain_guest):
                        ok = True
                        break

        except Exception:
            ok = False

        # --- 3) Hành động theo kết quả ---
        if ok:
            self._send("open passcode")
            if log_access:
                try:
                    log_access(method="passcode", result="granted")
                except Exception:
                    pass
        else:
            if log_access:
                try:
                    log_access(method="passcode", result="denied")
                except Exception:
                    pass

    # ---------- Fingerprint logging ----------
    def _log_fingerprint(self, granted: bool, finger_id: str | None):
        """
        Ghi log khi sensor vân tay báo kết quả.
        ESP32 TỰ MỞ CỬA nếu granted=True, nên PC chỉ log.
        """
        try:
            from .log_service import log_access  # type: ignore
        except Exception:
            log_access = None  # type: ignore

        if not log_access:
            return

        try:
            if granted:
                log_access(method="fingerprint", result="granted")
            else:
                log_access(method="fingerprint", result="denied")
        except Exception:
            pass
