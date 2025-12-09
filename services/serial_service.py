# services/serial_service.py
import os
import time
import threading
from dotenv import load_dotenv

load_dotenv()

try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None


def _clean_port_value(s: str) -> str:
    """Bỏ comment sau ; hoặc # và trim khoảng trắng."""
    if not s:
        return ""
    for sep in (";", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    return s.strip()


def _auto_detect_port() -> str | None:
    if list_ports is None:
        return None
    ports = list(list_ports.comports())
    if not ports:
        return None
    keywords = ("CP210", "CH340")
    scored = []
    for p in ports:
        desc = (p.description or "") + " " + (p.hwid or "")
        score = sum(k in desc for k in keywords)
        scored.append((score, p.device))
    scored.sort(reverse=True)
    return scored[0][1] if scored else ports[0].device


class SerialService:
    """
    Kết nối tới ESP32 qua USB (PC <-> sys.stdin/sys.stdout của ESP32).
    Nếu không có phần cứng → dummy mode (available=False).
    """

    def __init__(self, on_message=None):
        self.on_message = on_message
        self.available = False
        self.ser = None
        self._running = False

        port_raw = os.getenv("SERIAL_PORT", "")
        port = _clean_port_value(port_raw)

        # ESP32 code đang dùng: uart = UART(1, baudrate=57600, ...) cho sensor,
        # còn USB REPL thường cũng config 57600/115200. Mặc định mình để 57600.
        baud = int(os.getenv("SERIAL_BAUD", "57600") or "57600")

        # Tự dò nếu trống/AUTO
        if not port or port.upper() == "AUTO":
            port = _auto_detect_port() or ""

        if serial is None or not port:
            self.available = False
            return

        try:
            self.ser = serial.Serial(port, baud, timeout=0.1)
            self.available = True
            self._running = True
            threading.Thread(target=self._rx_loop, daemon=True).start()
        except Exception:
            self.available = False
            self.ser = None

    def _rx_loop(self):
        buf = ""
        while self._running and self.ser:
            try:
                if self.ser.in_waiting:
                    c = self.ser.read().decode(errors="ignore")
                    if c == "\n":
                        line = buf.strip()
                        buf = ""
                        if not line:
                            continue
                        # BỎ spam LED
                        if "LED set success" in line:
                            continue
                        if self.on_message:
                            self.on_message(line)
                    elif c != "\r":
                        buf += c
                else:
                    time.sleep(0.01)
            except Exception:
                time.sleep(0.2)

    def send(self, line: str):
        if not self.available or not self.ser:
            return
        try:
            self.ser.write((line.strip() + "\n").encode())
        except Exception:
            pass

    def close(self):
        self._running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
