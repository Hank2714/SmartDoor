# services/recog_daemon.py
from __future__ import annotations
import time
import threading
from typing import Optional, Callable, Dict, Any

import numpy as np

from services.face_service import recognize_with_box, THRESHOLD as DEFAULT_THR
from services.settings_service import get_all_settings   # <-- NEW


class RecognitionDaemon(threading.Thread):
    """
    Nhận diện khuôn mặt định kỳ:
      - on_visual(viz) để UI vẽ khung (box/label/color/ts)
      - Đếm giữ match_hold_ms rồi gọi on_hit(name, dist) & pause()
      - resume() để tiếp tục sau khi cửa đóng
    """

    def __init__(
        self,
        last_frame_supplier: Callable[[], Optional[np.ndarray]],
        on_status: Callable[[str], None],
        on_hit: Callable[[str, float], None],
        on_visual: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None,
        period_sec: float = 0.8,
        threshold: float = DEFAULT_THR,
        denied_log_cooldown_ms: int = 5000,
        matched_cooldown_ms: int = 2000,
        match_hold_ms: int = 2000,
    ):
        super().__init__(daemon=True)
        self._get_frame = last_frame_supplier
        self._on_status = on_status
        self._on_hit = on_hit
        self._on_visual = on_visual or (lambda *_: None)

        self._period = max(0.3, float(period_sec))
        self._thr = float(threshold)
        self._deny_cd = int(denied_log_cooldown_ms)
        self._match_cd = int(matched_cooldown_ms)
        self._match_hold_ms = int(match_hold_ms)

        self._stop = threading.Event()
        self._paused = threading.Event()

        self._last_deny_ts = 0.0
        self._last_match_ts = 0.0

        self._pending_name: Optional[str] = None
        self._pending_dist: float = 0.0
        self._pending_since: float = 0.0

    def pause(self):
        self._paused.set()
        try:
            self._on_visual(None)
        except Exception:
            pass
        self._pending_name = None
        self._pending_since = 0.0

    def resume(self):
        self._paused.clear()
        self._last_match_ts = 0.0
        self._last_deny_ts = 0.0
        self._pending_name = None
        self._pending_since = 0.0
        try:
            self._on_status("Face: resumed")
        except Exception:
            pass

    def stop(self):
        self._stop.set()

    def run(self):
        self._on_status("Face: ready")
        while not self._stop.is_set():
            t0 = time.time()
            try:
                # Nếu đang pause (do cửa mở, v.v.) thì bỏ qua vòng lặp
                if self._paused.is_set():
                    self._sleep_rest(t0)
                    continue

                # === NEW: đọc setting, nếu tắt face_recognition thì không nhận diện ===
                face_enabled = True
                try:
                    s = get_all_settings() or {}
                    face_enabled = bool(s.get("face_recognition_enabled", 1))
                except Exception:
                    face_enabled = True  # nếu lỗi DB thì coi như bật để không "chết" tính năng

                if not face_enabled:
                    # clear state + overlay
                    self._pending_name = None
                    self._pending_since = 0.0
                    self._last_match_ts = 0.0
                    self._last_deny_ts = 0.0
                    try:
                        self._on_visual(None)
                        self._on_status("Face: disabled")
                    except Exception:
                        pass
                    self._sleep_rest(t0)
                    continue
                # === END NEW BLOCK ===

                frame = self._get_frame()
                if frame is None:
                    self._on_status("Face: no frame")
                    self._on_visual(None)
                    self._sleep_rest(t0)
                    continue

                # Đang giữ pending match để chờ mở cửa
                if self._pending_name is not None:
                    hold_left = self._match_hold_ms / 1000.0 - (time.time() - self._pending_since)
                    if hold_left <= 0:
                        try:
                            self._on_hit(self._pending_name, float(self._pending_dist))
                        except Exception:
                            pass
                        self._pending_name = None
                        self._pending_since = 0.0
                        self.pause()
                        self._sleep_rest(t0)
                        continue
                    else:
                        try:
                            self._on_status(f"Face: ✅ {self._pending_name} — opening in {hold_left:.1f}s")
                        except Exception:
                            pass
                        matched, name, dist, box = recognize_with_box(frame, threshold=self._thr)
                        if box is not None:
                            x0, y0, x1, y1 = box
                            label = f"{self._pending_name}"
                            color = (60, 220, 100)
                            self._on_visual({
                                "box": (x0, y0, x1, y1),
                                "label": label,
                                "color": color,
                                "ts": time.time()
                            })
                        else:
                            self._on_visual(None)
                        self._sleep_rest(t0)
                        continue

                # Nhận diện mới
                matched, name, dist, box = recognize_with_box(frame, threshold=self._thr)

                if box is not None:
                    x0, y0, x1, y1 = box
                    if matched and name:
                        label = f"{name}"
                        color = (60, 220, 100)
                    else:
                        label = "Unknown"
                        color = (60, 180, 255)
                    self._on_visual({"box": (x0, y0, x1, y1), "label": label, "color": color, "ts": time.time()})
                else:
                    self._on_visual(None)

                now = time.time()
                if matched and name:
                    if (now - self._last_match_ts) * 1000.0 >= self._match_cd:
                        self._last_match_ts = now
                        self._pending_name = str(name)
                        self._pending_dist = float(dist)
                        self._pending_since = time.time()
                        try:
                            self._on_status(f"Face: ✅ {name} — opening in {self._match_hold_ms/1000:.1f}s")
                        except Exception:
                            pass
                else:
                    if (now - self._last_deny_ts) * 1000.0 >= self._deny_cd:
                        self._last_deny_ts = now
                        try:
                            self._on_status("Face: no match")
                        except Exception:
                            pass

            except Exception:
                try:
                    self._on_status("Face: error")
                except Exception:
                    pass
            finally:
                self._sleep_rest(t0)

        try:
            self._on_visual(None)
            self._on_status("Face: stopped")
        except Exception:
            pass

    def _sleep_rest(self, t0: float):
        remain = max(0.0, self._period - (time.time() - t0))
        self._stop.wait(remain)
